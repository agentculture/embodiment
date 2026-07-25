"""The bounded agentic tool-loop — perceive, decide, act, bounded by a budget.

The loop is the pump: handed a ``complete`` callable that performs *one* model
turn (given the running message list, return the assistant's reply and any tool
calls), it drives that callable until the model finishes, stops asking for
tools, or runs out of steps. It knows nothing about which model answers, which
tools exist, or which surface the operator is sitting at — all three arrive as
injected seams, so one loop is written here and imported per host instead of
reimplemented.

Termination is an honesty condition
-----------------------------------
:func:`_work_loop` has **exactly three** exits, and they are the only ones:

* :data:`EXIT_FINISHED` — the tool executor reported a finish;
* :data:`EXIT_STOPPED` — the model ended a turn asking for no tool and, even
  after the configured nudges, never finished;
* :data:`EXIT_BUDGET` — ``max_steps`` model turns were taken without finishing.

``_work_loop`` contains no ``raise`` of its own and returns nothing but those
three constants; ``tests/test_loop.py`` proves both structurally by AST. The one
remaining way out is an exception raised by the *injected* ``complete`` or
executor — the host's own failure surfacing — which :func:`run` catches to
finalize the partial :class:`~embodiment.contract.TaskResult` and re-raise as
:class:`LoopAborted`. Hooks add no exit path and cannot extend the budget: the
nudge for a prose-only turn spends model turns from the same ``max_steps``.

The hook lifecycle
------------------
Four events fire — ``task_start`` (once, before the loop), ``pre_tool`` (before
each tool executes), ``post_tool`` (after each tool *attempt*), and ``finish``
(once, on every loop exit including the aborted path). **Only ``pre_tool`` is
control-bearing:** its first decisive decision wins, where ``deny`` skips
execution (the reason is fed back to the model as the tool result) and
``rewrite`` swaps the call's arguments. ``task_start`` / ``post_tool`` /
``finish`` are observe-only — a ``deny`` or ``rewrite`` returned on them is
recorded and ignored. Every firing is appended to
:attr:`LoopOutcome.hook_firings` in order.

Hook *discovery* is not the loop's business. Upstream this module read
``.colleague/hooks.json`` from disk and spawned each entry as a child process;
here the whole thing arrives as one injected :data:`HookFn`, which is what keeps
process spawning, config-directory resolution and command quoting out of this
package entirely (constraint C6: nothing in ``embodiment`` may assume a shell,
because not every host that wants a presence has one).

Provenance and the extraction boundary (task t4)
------------------------------------------------
Extracted from colleague ``1.52.1``'s ``colleague/loop.py`` (4463 lines), whose
seams were already injection-shaped. Carried across: the turn loop and its three
exits, the per-call tool lifecycle, the hook lifecycle, the message-shaping
helpers, the initial-prompt build (including media content parts), the bounded
context window plus its shrink-and-retry degradation, the media-rejection
flatten, forced final synthesis, literal-finish-markup recovery, terminal
summary precedence, and the honest-incompletion classifier.

Deliberately left behind, because each served a colleague *policy* rather than
the loop — a reader expecting them should find them at their owning layer, not
here:

* a **default tool executor** — the host injects one, always (there is no
  ``ToolExecutor(repo_path)`` fallback, and no ``run_command`` approval policy:
  authority over a tool belongs to whoever built the executor);
* **subagent fan-out** (``Spawns``, ``MAX_SUBAGENT_FANOUT``) — one ``int`` that
  dragged in a 3000-line config module; a host that delegates does it inside its
  own executor;
* the **pre-finish gates** (lint, test-integrity, affected-tests, coherence) and
  the **memory** recall/remember pair — the last two return as the
  :data:`ContinuityFn` boundaries below, which task t14 fills in;
* **fill-line / compaction / auto-split / backpressure / capacity / chaining**
  — context *economics*, one layer up from windowing;
* **flight control and telemetry** — both collapse into the single injected
  :data:`ObserverFn`, and the pilot-stop exit they justified is gone with them;
* the **unknown-tool streak guard**, whose ``tool_protocol`` exit was a fourth
  way out of the loop; an unknown tool now costs one self-correcting step and
  the budget bounds the damage;
* the **thin / meta finish** synthesis guards, which keyed off ``write_file`` /
  ``edit_file`` appearing in ``stats.tool_counts`` — a hard-coded tool surface
  this loop is not allowed to assume;
* **self-knowledge, senses injection, deepthink escalation, neighbour clones,
  escalation records** — product surfaces, not the pump.

Every ``contextlib.suppress(Exception)`` upstream became an explicit handler
that records a :class:`LoopDegradation` (constraint C3): observability may never
control the loop, but it may not fail silently either.
"""

from __future__ import annotations

import datetime
import json
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol, Sequence, Union, runtime_checkable

from embodiment.context import classify_degradable, is_media_rejection, window_messages
from embodiment.contract import (
    ERROR,
    INCOMPLETE,
    NO_RESULT_PRODUCED,
    OK,
    ContextPacket,
    IncompletionRecord,
    ModelResponse,
    Step,
    Task,
    TaskResult,
    ToolCall,
    WorkAborted,
)
from embodiment.media import build_part, flatten_parts

__all__ = [
    # exits
    "EXIT_FINISHED",
    "EXIT_STOPPED",
    "EXIT_BUDGET",
    "EXIT_ABORTED",
    "EXIT_REASONS",
    # hook lifecycle
    "EVENT_TASK_START",
    "EVENT_PRE_TOOL",
    "EVENT_POST_TOOL",
    "EVENT_FINISH",
    "HOOK_EVENTS",
    "CONTROL_BEARING_EVENTS",
    "DECISION_ALLOW",
    "DECISION_DENY",
    "DECISION_REWRITE",
    "DECISION_OBSERVE",
    "HookEvent",
    "HookDecision",
    "HookFiring",
    "HookFn",
    # tools
    "ToolError",
    "UnknownToolError",
    "ToolOutcome",
    "ToolExecutor",
    # observability
    "LoopEvent",
    "LoopDegradation",
    "ObserverFn",
    "ProgressFn",
    "DEGRADED_ATTACHMENT",
    "DEGRADED_CONTEXT_OVERFLOW",
    "DEGRADED_CONTINUITY",
    "DEGRADED_HOOK_ERROR",
    "DEGRADED_MEDIA_REJECTED",
    "DEGRADED_OBSERVER",
    "DEGRADED_OVERFLOW_EXHAUSTED",
    "DEGRADED_PRESENCE",
    "DEGRADED_PROGRESS",
    "DEGRADED_SYNTHESIS",
    # presence + continuity seams
    "PresenceSink",
    "OperatorInboxFn",
    "Boundary",
    "ContinuityFn",
    "BOUNDARY_ACTION",
    "BOUNDARY_COMPLETION",
    "BOUNDARY_MEMORY",
    "LOOP_BOUNDARIES",
    # driving
    "CompleteFn",
    "LoopControls",
    "LoopOutcome",
    "LoopAborted",
    "run",
    # re-exported contract shapes a host needs to build a seam
    "ModelResponse",
    "ToolCall",
    "WorkAborted",
]


# ── exits ─────────────────────────────────────────────────────────────────────

#: The finish signal came back from the tool executor — an authoritative result.
EXIT_FINISHED = "finished"
#: The model ended a turn requesting no tool and never finished (a partial).
EXIT_STOPPED = "stopped"
#: ``max_steps`` model turns were spent without finishing (a partial).
EXIT_BUDGET = "budget"
#: The complete set of ways ``_work_loop`` can END. There is no fourth; see the
#: module docstring, and the AST tests that prove it structurally.
EXIT_REASONS = (EXIT_FINISHED, EXIT_STOPPED, EXIT_BUDGET)
#: NOT a loop exit — the state when the loop did not exit at all because an
#: injected seam raised. ``_work_loop`` never returns this and a test proves it;
#: only :func:`run` sets it, on the path where it catches, finalizes the partial
#: work, and re-raises as :class:`LoopAborted`.
#:
#: It exists because the alternative was dishonest: this defaulted to
#: ``EXIT_BUDGET``, so an abort three steps into a twenty-step drive reported
#: ``exit_reason="budget"`` — the loop claiming it had exhausted a budget it had
#: barely touched. Found by an independent review of this branch.
EXIT_ABORTED = "aborted"


# ── hook lifecycle ────────────────────────────────────────────────────────────

EVENT_TASK_START = "task_start"
EVENT_PRE_TOOL = "pre_tool"
EVENT_POST_TOOL = "post_tool"
EVENT_FINISH = "finish"
#: The four lifecycle events, in the order a full drive fires them.
HOOK_EVENTS = (EVENT_TASK_START, EVENT_PRE_TOOL, EVENT_POST_TOOL, EVENT_FINISH)
#: The events whose decisions the loop acts on. Exactly one, by design.
CONTROL_BEARING_EVENTS = (EVENT_PRE_TOOL,)

DECISION_ALLOW = "allow"
DECISION_DENY = "deny"
DECISION_REWRITE = "rewrite"
DECISION_OBSERVE = "observe"
#: Decisions that end the ``pre_tool`` scan. ``allow``/``observe`` never do.
_DECISIVE = (DECISION_DENY, DECISION_REWRITE)


# ── degradation vocabulary (C3) ───────────────────────────────────────────────

#: An attachment named by the task could not be read into a content part.
DEGRADED_ATTACHMENT = "attachment-unreadable"
#: A degradable completion error was retried against a smaller window.
DEGRADED_CONTEXT_OVERFLOW = "context-overflow-retry"
#: The shrink-and-retry ladder hit its floor or cap; the partial is preserved.
DEGRADED_OVERFLOW_EXHAUSTED = "context-overflow-exhausted"
#: The endpoint refused media parts; the history was flattened to text.
DEGRADED_MEDIA_REJECTED = "media-rejected"
#: The injected hook runner raised; the firing failed closed to a deny.
DEGRADED_HOOK_ERROR = "hook-error"
#: The injected progress sink raised. Observability never controls the loop.
DEGRADED_PROGRESS = "progress-sink-failed"
#: The injected observer raised; it is disabled for the rest of the drive.
DEGRADED_OBSERVER = "observer-failed"
#: The injected presence sink raised; the drive continues unattended.
DEGRADED_PRESENCE = "presence-failed"
#: The injected continuity seam raised at a boundary.
DEGRADED_CONTINUITY = "continuity-failed"
#: The forced final synthesis turn failed; the summary falls to its next rung.
DEGRADED_SYNTHESIS = "synthesis-failed"


# ── loop constants ────────────────────────────────────────────────────────────

#: Each reactive retry multiplies the window budget by this factor.
_OVERFLOW_SHRINK_FACTOR = 0.6

#: How many nudges a prose-only turn gets before the loop stops. Each nudge
#: costs a model turn from ``max_steps`` — a nudge can never buy an extra one.
_DEFAULT_CONTINUE_NUDGES = 1

_FINISH_NUDGE = (
    "You ended your turn without finishing and without requesting another tool. "
    "If your work is complete, finish now with your result as the summary. "
    "Otherwise continue by calling a tool — do not reply with prose alone."
)
_SYNTHESIS_PROMPT = (
    "You are out of steps. Stop using tools and answer the original request NOW, "
    "directly, from what you have already read. Do not request any more tools — write "
    "the most complete, useful answer you can from the context gathered so far."
)
_EMPTY_FINISH_PROMPT = (
    "You finished without a summary, but the summary IS your deliverable. Write your "
    "complete result NOW, directly, from what you have already read. Do not request "
    "any more tools."
)

#: The default system prompt names no tool: this loop must not assume a file or
#: shell surface exists (constraint C6). A host that has one says so itself.
_DEFAULT_SYSTEM = (
    "You are an agent working through a task with the tools you have been given. "
    "Use them to gather what you need, then finish with a short, complete summary "
    "of the result. Make the smallest change that satisfies the task."
)

#: Phase notices, fired through the progress sink with an EMPTY tool name — a
#: reserved sentinel, since a real tool always has one — so a sink renders them
#: as a standalone line rather than a step. A long completion is otherwise
#: indistinguishable from a hang.
_PHASE_THINKING = "thinking… (waiting on the model — this can be slow on a large model)"
_PHASE_SYNTHESIZING = (
    "synthesizing the final answer from what was read — this can take a while on a "
    "slow backend; it is working, not stalled…"
)

#: Literal finish-markup recovery: a served model sometimes emits its finish as
#: literal tool-call markup inside message content instead of a structured call.
#: The report exists — only the transport failed — so the loop re-parses that
#: shape rather than losing it to the nudge/stop path. Scanned with linear
#: ``str.find`` (no regex) so adversarial content cannot backtrack.
_FINISH_MARKER = "function=finish"
_SUMMARY_OPEN = "<parameter=summary>"
_SUMMARY_CLOSE = "</parameter>"


# ── the tool seam ─────────────────────────────────────────────────────────────


class ToolError(Exception):
    """A tool call the host's executor cannot honor (bad path, missing file, …).

    Costs exactly one non-ok step with a self-correcting message fed back to the
    model — never the run. Hosts raise it from :meth:`ToolExecutor.execute`.
    """


class UnknownToolError(ToolError):
    """A call naming a tool the host does not have.

    Distinguished from a plain :class:`ToolError` so a host can tell a broken
    tool-call *protocol* from an ordinary bad call to a real tool. The loop
    itself treats both identically — one self-correcting step — because a
    special exit for a broken channel would be a fourth way out of the loop.
    """


@dataclass
class ToolOutcome:
    """The result of executing one tool call.

    ``finished`` is how a host signals the model's finish: the loop reads it,
    never a tool *name*, so a host is free to call its terminal tool anything.
    """

    result: str
    changed_file: Optional[str] = None
    finished: bool = False
    finish_summary: str = ""
    destination: Optional[str] = None
    """A goal-frame slug the work item aimed at, when the host tracks one."""
    announcement: Optional[str] = None
    """The arrival announcement declared on finish, when the host tracks one."""
    media_part: Optional[dict[str, Any]] = None
    """An OpenAI content part produced by a media-viewing tool. The tool message
    itself stays a plain string (the wire-safe convention); a non-``None`` part
    rides a follow-up user parts message the next turn sees."""


@runtime_checkable
class ToolExecutor(Protocol):
    """The structural shape of the injected tool surface — one required method.

    The loop never constructs an executor and never enumerates tools: what the
    model may do is entirely the host's to decide, which is what keeps
    ``embodiment`` free of any shell or filesystem coupling (constraint C6).

    Three OPTIONAL ledger attributes are read defensively when present, because
    a minimal stand-in with nothing but ``execute`` must drive a whole run:

    * ``changed`` — an iterable of paths the run wrote (→ ``changed_files``);
    * ``bytes_written`` — an int (→ ``stats.bytes_written``);
    * ``sub_results`` — a list of :class:`~embodiment.contract.SubResult`.
    """

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome: ...


# ── the hook seam ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class HookEvent:
    """What the loop hands the injected hook runner at one lifecycle event."""

    event: str
    task: Task
    tool: Optional[str] = None
    arguments: Optional[dict[str, Any]] = None
    step_index: int = 0

    def payload(self) -> dict[str, Any]:
        """A JSON-ready payload, for a host that relays hooks to another process."""
        return {
            "event": self.event,
            "tool": self.tool,
            "arguments": self.arguments,
            "task_id": self.task.id,
            "repo_path": self.task.repo_path,
            "step_index": self.step_index,
        }


@dataclass(frozen=True)
class HookDecision:
    """One hook's verdict.

    Only ``deny`` and ``rewrite`` are control-bearing, and only on ``pre_tool``.
    ``source`` labels which hook spoke — a command, a plugin name, whatever the
    host's hook system calls things; the loop only records it.
    """

    decision: str
    arguments: Optional[dict[str, Any]] = None
    reason: str = ""
    exit_code: Optional[int] = None
    source: str = ""

    @property
    def control_bearing(self) -> bool:
        return self.decision in _DECISIVE


@dataclass(frozen=True)
class HookFiring:
    """The record of one hook having run, appended in order.

    The contract module deliberately does not carry this shape: the hook
    lifecycle belongs to the loop, so its record does too.
    """

    event: str
    tool: Optional[str]
    source: str
    decision: str
    exit_code: Optional[int] = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.event,
            "tool": self.tool,
            "source": self.source,
            "decision": self.decision,
            "exit_code": self.exit_code,
            "reason": self.reason,
        }


#: The injected hook runner: given one :class:`HookEvent`, return the decisions
#: of every hook that ran, in order — a sequence, a single decision, or ``None``
#: for "nothing fired". Discovery, matching and execution are all the host's.
HookFn = Callable[[HookEvent], Union[None, HookDecision, Sequence[HookDecision]]]


# ── observability seams ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class LoopEvent:
    """One thing the loop did, offered to the injected observer.

    ``kind`` is a stable token (``turn`` / ``step`` / ``hook`` / ``phase`` /
    ``degradation`` / ``exit``); branch on it, never on ``detail``'s free text.
    """

    kind: str
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LoopDegradation:
    """One recorded, host-visible degradation (constraint C3).

    Never inferred: every non-nominal path constructs one of these and puts it
    on :attr:`LoopOutcome.degradations`, rather than silently returning a value
    that looks like success. ``code`` is from the ``DEGRADED_*`` vocabulary.
    """

    code: str
    reason: str
    step_index: int = 0
    model_turns: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "reason": self.reason,
            "step_index": self.step_index,
            "model_turns": self.model_turns,
        }


#: A ``complete`` performs one model turn given the running message list.
CompleteFn = Callable[[list[dict[str, Any]]], ModelResponse]

#: A per-step progress sink ``(step_index, tool, arguments, ok)``. The
#: *arguments* are handed over raw: upstream this position carried a label
#: rendered by a TUI module, which is exactly the front dependency this package
#: must not have — a host that wants a label renders its own. A phase notice
#: arrives with an empty ``tool`` and the notice text in the third position.
ProgressFn = Callable[[int, str, Any, bool], None]

#: An observability sink. Replaces both the telemetry span tree and the flight
#: feed upstream. Never control-bearing; a raise disables it and is recorded.
ObserverFn = Callable[[LoopEvent], None]


# ── the presence seam ─────────────────────────────────────────────────────────


@runtime_checkable
class PresenceSink(Protocol):
    """What the loop calls to keep an app feeling attended-to between acts.

    Deliberately re-declared here rather than imported from
    :mod:`embodiment.presence_engine`: presence *consumes* the loop, never the
    reverse, and this module must stay importable without pulling the pump in.
    The shape is the pump's ``PresenceSink`` exactly, so the real
    ``PresenceEngine`` satisfies it structurally (``tests/test_loop.py`` proves
    it by driving one).

    With no sink — or an inactive one — the loop is byte-identical to a loop
    with no presence at all: nothing is called, nothing is recorded.
    """

    @property
    def active(self) -> bool: ...

    def acknowledge(self, packet: Optional[ContextPacket]) -> Any: ...

    def on_operator_message(self, text: str) -> Any: ...

    def on_progress_boundary(self, *, step_count: int = 0, phase_changed: bool = False) -> Any: ...


#: Polled once per turn boundary for pending operator text. Non-blocking by
#: contract; the loop routes each message to the presence sink and does NOT
#: inject it into the model conversation — deciding whether an operator's aside
#: becomes guidance for the acting model is host policy, not the pump's.
OperatorInboxFn = Callable[[], Optional[Sequence[str]]]


# ── the continuity seam (task t14's injection point) ──────────────────────────

#: Before a consequential action — fired per tool call, before ``pre_tool``.
BOUNDARY_ACTION = "before-action"
#: Before final completion — fired once, after the loop, before the summary is
#: resolved.
BOUNDARY_COMPLETION = "before-completion"
#: Before durable memory — fired once, last, with the result fully populated.
BOUNDARY_MEMORY = "before-memory"
#: The three boundaries issue #2 names. The loop owns *when*; it owns no policy.
LOOP_BOUNDARIES = (BOUNDARY_ACTION, BOUNDARY_COMPLETION, BOUNDARY_MEMORY)


@dataclass(frozen=True)
class Boundary:
    """The lived-sequence position handed to the continuity seam."""

    name: str
    task: Task
    result: TaskResult
    step_index: int = 0
    model_turns: int = 0
    tool: Optional[str] = None
    arguments: Optional[dict[str, Any]] = None


#: The continuity injection point. The loop CALLS it and ignores whatever comes
#: back: coherence asks whether an action makes *sense*, while whether it is
#: *permitted* belongs to the capability layer (the hook seam above and the
#: host's own executor). Task t14 fills this in; nothing here is policy.
ContinuityFn = Callable[[Boundary], Any]


# ── controls ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LoopControls:
    """The loop's own knobs. Every default is the strict no-op.

    Fields
    ------
    context_budget:
        Token budget the running history is windowed to before every turn, and
        the starting point of the shrink-and-retry ladder. ``None`` disables
        both — no windowing, and a degradable error propagates on first sight.
    count_tokens:
        The counter handed to :func:`~embodiment.context.window_messages`;
        ``None`` uses its char estimate.
    max_overflow_retries:
        Cap on the reactive shrink-and-retry attempts after a degradable error.
    max_continue_nudges:
        How many prose-only turns are nudged toward finishing before the loop
        stops. Each nudge spends a model turn from ``max_steps``.
    synthesis:
        Whether a summary-less exit gets ONE forced no-tools synthesis turn, so
        a full-token run that never answered still returns a usable partial.
        The turn is paid for out of ``max_steps``, not added to it: arming this
        reserves one model turn from the reading budget and the synthesis is
        skipped outright once the budget is spent, so the total number of
        ``complete`` calls a drive makes never exceeds ``max_steps``.
    incompletion:
        Whether a run that produced no deliverable is flagged with an
        :class:`~embodiment.contract.IncompletionRecord`.
    write_intent:
        Whether this task was expected to change files — the one input the
        incompletion classifier cannot derive. Upstream this came from a
        role-registry lookup; here the host states it.
    """

    context_budget: Optional[int] = None
    count_tokens: Optional[Callable[[list[dict[str, Any]]], int]] = None
    max_overflow_retries: int = 3
    max_continue_nudges: int = _DEFAULT_CONTINUE_NUDGES
    synthesis: bool = True
    incompletion: bool = True
    write_intent: bool = True


@dataclass(frozen=True)
class LoopOutcome:
    """What one drive produced: the result, how it ended, and the loop's ledgers.

    ``hook_firings`` and ``degradations`` live here rather than on
    :class:`~embodiment.contract.TaskResult` because they are the *loop's* record
    of its own conduct, not part of the artifact shape every seam shares.
    """

    result: TaskResult
    exit_reason: str
    hook_firings: list[HookFiring] = field(default_factory=list)
    degradations: list[LoopDegradation] = field(default_factory=list)


class LoopAborted(WorkAborted):
    """The injected seam raised mid-loop; carries the partial work.

    A :class:`~embodiment.contract.WorkAborted` (so a host catching that still
    catches this) whose :attr:`outcome` additionally carries the loop's own
    ledgers, which the contract's exception has no field for. The original
    exception is the ``__cause__``.
    """

    def __init__(self, outcome: LoopOutcome) -> None:
        super().__init__(outcome.result)
        self.outcome = outcome


# ── the drive context ─────────────────────────────────────────────────────────


@dataclass
class _Work:
    """The fixed collaborators threaded through one drive's helpers.

    ``result`` and ``messages`` are mutated *through* these references; the
    remaining fields are set once by :func:`run` and read thereafter.
    """

    complete: CompleteFn
    executor: ToolExecutor
    task: Task
    result: TaskResult
    messages: list[dict[str, Any]]
    controls: LoopControls
    hooks: Optional[HookFn] = None
    progress: Optional[ProgressFn] = None
    observer: Optional[ObserverFn] = None
    presence: Optional[PresenceSink] = None
    operator_inbox: Optional[OperatorInboxFn] = None
    continuity: Optional[ContinuityFn] = None
    firings: list[HookFiring] = field(default_factory=list)
    degradations: list[LoopDegradation] = field(default_factory=list)
    last_substantive: str = ""
    observer_failed: bool = False
    presence_armed: bool = False
    #: The whole drive's model-turn budget — the reading budget the turn loop
    #: runs against PLUS any reserved synthesis turn. Nothing spends past it.
    turn_budget: int = 1


# ── observability helpers (never control-bearing, never silent) ───────────────


def _observe(ctx: _Work, kind: str, detail: str = "", **data: Any) -> None:
    """Offer one event to the injected observer.

    A raising observer is recorded ONCE and then disabled for the rest of the
    drive — retrying a broken sink would spam the ledger, and dropping it
    silently would hide a real breakage.
    """
    if ctx.observer is None or ctx.observer_failed:
        return
    try:
        ctx.observer(LoopEvent(kind=kind, detail=detail, data=dict(data)))
    except Exception as exc:  # noqa: BLE001 - observability never aborts a drive
        ctx.observer_failed = True
        _degrade(ctx, DEGRADED_OBSERVER, f"{type(exc).__name__}: {exc}")


def _degrade(ctx: _Work, code: str, reason: str) -> None:
    """Record a degradation on the ledger and offer it to the observer (C3)."""
    record = LoopDegradation(
        code=code,
        reason=reason,
        step_index=len(ctx.result.steps),
        model_turns=ctx.result.stats.model_turns,
    )
    ctx.degradations.append(record)
    _observe(ctx, "degradation", reason, **record.to_dict())


def _emit_progress(ctx: _Work, step_index: int, tool: str, arguments: Any, ok: bool) -> None:
    """Fire the per-step progress sink. Observability, never control."""
    _observe(ctx, "step", tool, step_index=step_index, ok=ok)
    if ctx.progress is None:
        return
    try:
        ctx.progress(step_index, tool, arguments, ok)
    except Exception as exc:  # noqa: BLE001 - a sink must never abort a drive
        _degrade(ctx, DEGRADED_PROGRESS, f"{type(exc).__name__}: {exc}")


def _emit_phase(ctx: _Work, detail: str) -> None:
    """Announce, through the same sink, that a model completion is in flight.

    Encoded with an EMPTY tool name so a sink renders a standalone phase line
    rather than a ``step N:`` line. The step index carries the LIVE step count.
    """
    _observe(ctx, "phase", detail)
    _presence_boundary(ctx, phase_changed=True)
    if ctx.progress is None:
        return
    try:
        ctx.progress(len(ctx.result.steps), "", detail, True)
    except Exception as exc:  # noqa: BLE001 - a sink must never abort a drive
        _degrade(ctx, DEGRADED_PROGRESS, f"{type(exc).__name__}: {exc}")


# ── the presence binding ──────────────────────────────────────────────────────


def _presence_arm(ctx: _Work) -> None:
    """Resolve whether the presence lane is live, once, before the first step.

    An inactive sink is indistinguishable from no sink: ``active`` is read once
    here and never again, so a sink cannot arm itself mid-drive and change a
    run's shape halfway through.
    """
    if ctx.presence is None:
        return
    try:
        ctx.presence_armed = bool(ctx.presence.active)
    except Exception as exc:  # noqa: BLE001 - presence never aborts a drive
        _degrade(ctx, DEGRADED_PRESENCE, f"{type(exc).__name__}: {exc}")


def _presence_acknowledge(ctx: _Work) -> None:
    """Call ``acknowledge`` exactly once, before the loop takes its first step."""
    if not ctx.presence_armed or ctx.presence is None:
        return
    try:
        ctx.presence.acknowledge(ctx.task.context_packet)
    except Exception as exc:  # noqa: BLE001 - presence never aborts a drive
        _degrade(ctx, DEGRADED_PRESENCE, f"{type(exc).__name__}: {exc}")


def _presence_boundary(ctx: _Work, *, phase_changed: bool = False) -> None:
    """Drive one progress beat — once per step, and once per phase notice."""
    if not ctx.presence_armed or ctx.presence is None:
        return
    try:
        ctx.presence.on_progress_boundary(
            step_count=len(ctx.result.steps), phase_changed=phase_changed
        )
    except Exception as exc:  # noqa: BLE001 - presence never aborts a drive
        _degrade(ctx, DEGRADED_PRESENCE, f"{type(exc).__name__}: {exc}")


def _drain_operator_inbox(ctx: _Work) -> None:
    """Poll the injected inbox at a turn boundary and route what it holds.

    The loop routes; it does not relay into the model conversation. Whether an
    operator's aside becomes guidance for the acting model is the host's call.
    """
    if ctx.operator_inbox is None:
        return
    try:
        pending = ctx.operator_inbox() or ()
    except Exception as exc:  # noqa: BLE001 - an inbox never aborts a drive
        _degrade(ctx, DEGRADED_PRESENCE, f"operator inbox: {type(exc).__name__}: {exc}")
        return
    for text in pending:
        _observe(ctx, "operator", text)
        if not ctx.presence_armed or ctx.presence is None:
            continue
        try:
            ctx.presence.on_operator_message(text)
        except Exception as exc:  # noqa: BLE001 - presence never aborts a drive
            _degrade(ctx, DEGRADED_PRESENCE, f"{type(exc).__name__}: {exc}")


# ── the continuity binding ────────────────────────────────────────────────────


def _boundary(ctx: _Work, name: str, **extra: Any) -> None:
    """Offer one lived-sequence boundary to the continuity seam.

    The return value is discarded by design (see :data:`ContinuityFn`), and a
    raising seam degrades: a memory or coherence subsystem that is down must
    never take the work item with it.
    """
    if ctx.continuity is None:
        return
    point = Boundary(
        name=name,
        task=ctx.task,
        result=ctx.result,
        step_index=len(ctx.result.steps),
        model_turns=ctx.result.stats.model_turns,
        **extra,
    )
    try:
        ctx.continuity(point)
    except Exception as exc:  # noqa: BLE001 - continuity never aborts a drive
        _degrade(ctx, DEGRADED_CONTINUITY, f"{name}: {type(exc).__name__}: {exc}")


# ── hook firing ───────────────────────────────────────────────────────────────


def _normalize_decisions(reply: Any) -> list[HookDecision]:
    """Accept ``None``, one decision, or a sequence of them; drop anything else."""
    if reply is None:
        return []
    if isinstance(reply, HookDecision):
        return [reply]
    if isinstance(reply, (str, bytes, dict)):
        return []
    try:
        items = list(reply)
    except TypeError:
        return []
    return [item for item in items if isinstance(item, HookDecision)]


def _fire_hooks(
    ctx: _Work,
    *,
    event: str,
    tool: Optional[str] = None,
    arguments: Optional[dict[str, Any]] = None,
) -> Optional[HookDecision]:
    """Run the injected hook runner for *event*; record a firing per decision.

    Returns the first control-bearing decision seen, or ``None``. Only the
    ``pre_tool`` caller acts on that return — for ``task_start`` / ``post_tool``
    / ``finish`` the caller discards it, which is what makes those three
    observe-only. ``allow`` / ``observe`` are recorded but never decisive, so
    the scan continues past them and the full sequence reaches the ledger.

    A hook runner that raises fails CLOSED: the failure is recorded as a deny
    firing plus a degradation, never propagated — a crashing hook cannot abort
    the work item, and on an observe-only event the deny is inert.
    """
    if ctx.hooks is None:
        return None
    payload = HookEvent(
        event=event,
        task=ctx.task,
        tool=tool,
        arguments=arguments,
        step_index=len(ctx.result.steps),
    )
    try:
        decisions = _normalize_decisions(ctx.hooks(payload))
    except Exception as exc:  # noqa: BLE001 - fail closed, never propagate
        reason = f"hook error: {type(exc).__name__}: {exc}"
        _degrade(ctx, DEGRADED_HOOK_ERROR, f"{event}: {reason}")
        decisions = [HookDecision(decision=DECISION_DENY, reason=reason)]

    decisive: Optional[HookDecision] = None
    for decision in decisions:
        ctx.firings.append(
            HookFiring(
                event=event,
                tool=tool,
                source=decision.source,
                decision=decision.decision,
                exit_code=decision.exit_code,
                reason=decision.reason,
            )
        )
        _observe(ctx, "hook", decision.decision, event=event, tool=tool, source=decision.source)
        if decisive is None and decision.control_bearing:
            decisive = decision
            # A decisive pre_tool verdict short-circuits the rest of the chain.
            if event == EVENT_PRE_TOOL:
                break
    return decisive


# ── message shaping ───────────────────────────────────────────────────────────


def _arguments_json(arguments: Any) -> str:
    """OpenAI wire format wants ``function.arguments`` as a JSON *string*.

    The loop carries arguments as dicts for execution; serialize only on the way
    back into the message list so strict servers accept replayed turns.
    """
    if isinstance(arguments, str):
        return arguments
    return json.dumps(arguments, ensure_ascii=False)


def _assistant_message(resp: ModelResponse) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": resp.content,
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": _arguments_json(tc.arguments)},
            }
            for tc in resp.tool_calls
        ],
    }


def _tool_message(call_id: str, content: str) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def _build_user_message(task: Task) -> str:
    """Compose the first user turn from instruction + context/constraints/goal."""
    user = task.instruction
    if task.context:
        user += f"\n\nContext:\n{task.context}"
    if task.constraints:
        user += "\n\nConstraints:\n" + "\n".join(f"- {c}" for c in task.constraints)
    if task.goal:
        user += f"\n\nGoal:\n{task.goal}"
    if task.acceptance:
        user += (
            "\n\nAcceptance criteria (the work is done when each of these holds):\n"
            + "\n".join(f"- {c}" for c in task.acceptance)
        )
    return user


def _build_initial_content(ctx: _Work) -> Union[str, list[dict[str, Any]]]:
    """The first user turn: a plain string, or content parts when media is attached.

    With no attachments this returns the string UNCHANGED — downstream
    string-assuming code (windowing, markup re-parse) must never meet a surprise
    list on an attachment-less run. An attachment that became unreadable between
    the host's validation and here degrades to a text placeholder naming the
    path and records a degradation: a broken attachment must never abort a run,
    and must never vanish quietly either.
    """
    text = _build_user_message(ctx.task)
    if not ctx.task.attachments:
        return text
    parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for attachment in ctx.task.attachments:
        try:
            parts.append(build_part(attachment))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            path = attachment.get("path", "?") if isinstance(attachment, dict) else "?"
            parts.append({"type": "text", "text": f"[attachment {path} unreadable: {exc}]"})
            _degrade(ctx, DEGRADED_ATTACHMENT, f"{path}: {type(exc).__name__}: {exc}")
    return parts


def _parse_literal_finish(content: str) -> Optional[str]:
    """Recover a finish summary from literal tool-call markup in message content.

    Returns the summary text, or ``None`` when the content is ordinary prose
    (the cheap substring guards keep the scan off the hot path).
    """
    marker = content.find(_FINISH_MARKER)
    if marker == -1:
        return None
    start = content.find(_SUMMARY_OPEN, marker)
    if start == -1:
        return None
    start += len(_SUMMARY_OPEN)
    end = content.find(_SUMMARY_CLOSE, start)
    if end == -1:
        return None
    return content[start:end].strip() or None


# ── the per-call tool lifecycle ───────────────────────────────────────────────


def _apply_finish(result: TaskResult, outcome: ToolOutcome) -> None:
    """Record a finish — summary, then the optional destination/announcement.

    The two optionals are set only when the host declared them, so a finish
    without a goal-frame leaves those keys off the artifact.
    """
    result.summary = outcome.finish_summary or result.summary
    if outcome.destination:
        result.destination = outcome.destination
    if outcome.announcement:
        result.announcement = outcome.announcement


def _record_failed_step(
    ctx: _Work, call: ToolCall, arguments: dict[str, Any], step_index: int, message: str
) -> None:
    """The shared shape of a step that did not execute or did not succeed."""
    ctx.result.steps.append(Step(step_index, call.name, arguments, message, ok=False))
    ctx.messages.append(_tool_message(call.id, message))
    _emit_progress(ctx, step_index, call.name, arguments, ok=False)
    _presence_boundary(ctx)


def _run_tool_call(ctx: _Work, call: ToolCall) -> bool:
    """Run one tool call through its lifecycle; return whether it finished.

    Owns the per-call sequence: the continuity boundary, the ``pre_tool`` hook
    (first deny/rewrite wins), execution, the ``post_tool`` hook, and finish
    detection. A denied or failed call still fires ``post_tool`` — the event
    observes the *attempt*, not only the success.
    """
    step_index = len(ctx.result.steps)
    arguments = call.arguments

    _boundary(ctx, BOUNDARY_ACTION, tool=call.name, arguments=dict(arguments))

    decision = _fire_hooks(ctx, event=EVENT_PRE_TOOL, tool=call.name, arguments=arguments)
    kind = decision.decision if decision is not None else None
    if kind == DECISION_DENY:
        reason = (decision and decision.reason) or "denied by a pre_tool hook"
        _record_failed_step(ctx, call, arguments, step_index, reason)
        _fire_hooks(ctx, event=EVENT_POST_TOOL, tool=call.name, arguments=arguments)
        return False
    if kind == DECISION_REWRITE and decision is not None and decision.arguments is not None:
        arguments = decision.arguments

    try:
        outcome = ctx.executor.execute(call.name, arguments)
    except (ToolError, KeyError, TypeError, ValueError) as exc:
        # ToolError is the executor's own contract. KeyError/TypeError/ValueError
        # are the argument-shaped residue of a malformed MODEL tool call. Either
        # way it costs ONE non-ok step with a self-correcting message — never the
        # run. Anything else (OSError, AttributeError, …) is a genuine harness
        # bug and still aborts loudly.
        msg = (
            str(exc)
            if isinstance(exc, ToolError)
            else f"bad tool arguments: {type(exc).__name__}: {exc}"
        )
        _record_failed_step(ctx, call, arguments, step_index, f"error: {msg}")
        _fire_hooks(ctx, event=EVENT_POST_TOOL, tool=call.name, arguments=arguments)
        return False

    ctx.result.steps.append(Step(step_index, call.name, arguments, outcome.result, ok=True))
    ctx.messages.append(_tool_message(call.id, outcome.result))
    if outcome.media_part is not None:
        # The tool message above stays a plain string (the wire-safe
        # convention); the media itself rides a follow-up user parts message.
        ctx.messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"[{call.name}] {outcome.result}"},
                    outcome.media_part,
                ],
            }
        )
    _emit_progress(ctx, step_index, call.name, arguments, ok=True)
    _presence_boundary(ctx)

    _fire_hooks(ctx, event=EVENT_POST_TOOL, tool=call.name, arguments=arguments)

    if not outcome.finished:
        return False
    _apply_finish(ctx.result, outcome)
    return True


def _run_tool_calls(ctx: _Work, calls: list[ToolCall]) -> bool:
    """Run every call in one model turn; return whether any of them finished.

    A finish does *not* cancel the rest of the turn — the remaining calls in the
    same response still run, and the loop exits once the turn completes.
    """
    finished = False
    for call in calls:
        if _run_tool_call(ctx, call):
            finished = True
    return finished


# ── completion with bounded degradation ───────────────────────────────────────


def _window_in_place(ctx: _Work, budget: int) -> None:
    """Trim the running history in place to *budget* tokens (head + tail kept).

    Mutating in place keeps the trim across turns — dropping old context is the
    intent, not an accident.
    """
    ctx.messages[:] = window_messages(ctx.messages, budget, ctx.controls.count_tokens)


def _shrink_for_retry(ctx: _Work, effective: int) -> Optional[int]:
    """Shrink one step and re-window; ``None`` at the floor.

    Each retry strictly shrinks the budget AND must reduce the message count.
    ``None`` means neither can shrink further, so retrying cannot help — this
    pairing is what guarantees the reactive ladder terminates.
    """
    shrunk = max(1, int(effective * _OVERFLOW_SHRINK_FACTOR))
    before = len(ctx.messages)
    _window_in_place(ctx, shrunk)
    if shrunk >= effective and len(ctx.messages) >= before:
        return None
    return shrunk


def _flatten_on_media_rejection(ctx: _Work, exc: Exception) -> bool:
    """Flatten every parts message and record the drop after a media refusal.

    Returns ``True`` when a retry should happen — the error named a media
    refusal AND at least one parts message existed to flatten, so the retry is
    structurally different from the attempt that failed. A text-only served
    model rejects an image part outright rather than ignoring it; the run
    continues text-only with the attachments recorded ``dropped``, instead of
    hard-failing on media the model was never able to take.
    """
    if not is_media_rejection(str(exc)):
        return False
    had_parts = False
    for i, message in enumerate(ctx.messages):
        if isinstance(message.get("content"), list):
            ctx.messages[i] = dict(message, content=flatten_parts(message["content"]))
            had_parts = True
    if not had_parts:
        return False
    if ctx.task.attachments and ctx.result.media is None:
        ctx.result.media = {
            "attachments": [
                {"path": str(a.get("path", "?")), "status": "dropped"}
                for a in ctx.task.attachments
                if isinstance(a, dict)
            ]
        }
    _degrade(ctx, DEGRADED_MEDIA_REJECTED, f"{exc}")
    return True


def _complete_with_degradation(ctx: _Work, *, phase: str = _PHASE_THINKING) -> ModelResponse:
    """Window the history, call ``complete``, and degrade on a degradable error.

    With no positive ``context_budget`` this is a thin pass-through: no
    windowing, ``complete`` is called once, and whatever it raises propagates —
    with ONE exception, the media-rejection flatten, which must not depend on
    the budget feature being on.

    With a budget: the history is windowed before the call, and a degradable
    error (a context overflow or a request timeout) shrinks the budget and
    retries. Every attempt either returns, raises, or strictly decrements one of
    two counters that are never incremented — so the ladder always terminates,
    and every rung it descends is recorded on the degradation ledger.
    """
    _emit_phase(ctx, phase)
    budget = ctx.controls.context_budget
    windowed = isinstance(budget, int) and budget > 0
    effective = int(budget) if windowed else 0
    if windowed:
        _window_in_place(ctx, effective)

    flattens_left = 1
    retries_left = max(0, ctx.controls.max_overflow_retries) if windowed else 0
    while True:
        try:
            return ctx.complete(ctx.messages)
        except Exception as exc:  # noqa: BLE001 - classified below, never swallowed
            if flattens_left > 0 and _flatten_on_media_rejection(ctx, exc):
                flattens_left -= 1
                continue
            if not windowed:
                raise
            if classify_degradable(str(exc)) is None:
                raise  # non-degradable errors are never retried
            if retries_left <= 0:
                _degrade(ctx, DEGRADED_OVERFLOW_EXHAUSTED, f"retry cap reached: {exc}")
                raise
            shrunk = _shrink_for_retry(ctx, effective)
            if shrunk is None:
                _degrade(ctx, DEGRADED_OVERFLOW_EXHAUSTED, f"window floor reached: {exc}")
                raise
            _degrade(
                ctx,
                DEGRADED_CONTEXT_OVERFLOW,
                f"{classify_degradable(str(exc))}: window {effective} -> {shrunk}",
            )
            effective = shrunk
            retries_left -= 1


# ── the turn loop ─────────────────────────────────────────────────────────────


def _account_turn(ctx: _Work, resp: ModelResponse) -> None:
    """Per-turn bookkeeping: usage, turn count, generated sizes, last prose."""
    ctx.result.usage.add(resp.prompt_tokens, resp.completion_tokens)
    ctx.result.stats.model_turns += 1
    ctx.result.stats.add_generated(reasoning=resp.reasoning, answer=resp.content)
    if resp.content:
        ctx.last_substantive = resp.content
    _observe(
        ctx,
        "turn",
        resp.content[:200],
        model_turns=ctx.result.stats.model_turns,
        tool_calls=len(resp.tool_calls),
    )


def _handle_no_tool_turn(ctx: _Work, resp: ModelResponse, nudges: int) -> tuple[int, Optional[str]]:
    """Handle a turn that requested no tool — nudge up to the cap, else stop.

    The contract is to finish; a bare prose turn is usually the model trailing
    off mid-task. Returns ``(nudges, exit_or_None)``. Under the cap it appends
    the prose plus a one-line reminder and lets the loop continue — spending a
    model turn from the same budget, never extending it. At the cap it returns
    :data:`EXIT_STOPPED` WITHOUT setting a summary: the trailing prose is often
    a mid-thought trail-off, so leaving the summary empty lets the forced
    synthesis produce a clean one, with the prose surviving as the floor.
    """
    recovered = _parse_literal_finish(resp.content or "")
    if recovered is not None:
        # The "no-tool turn" may actually BE the finish, emitted as literal
        # markup. Re-parse it rather than nudging a model that already answered.
        ctx.result.summary = recovered
        ctx.result.finish_recovered = "literal-markup"
        return nudges, EXIT_FINISHED
    if nudges < ctx.controls.max_continue_nudges:
        if resp.content:
            ctx.messages.append({"role": "assistant", "content": resp.content})
        ctx.messages.append({"role": "user", "content": _FINISH_NUDGE})
        return nudges + 1, None
    return nudges, EXIT_STOPPED


def _advance_turn(ctx: _Work, resp: ModelResponse, nudges: int) -> tuple[int, Optional[str]]:
    """Process one turn; return ``(nudges, exit_reason_or_None)``."""
    if not resp.tool_calls:
        return _handle_no_tool_turn(ctx, resp, nudges)
    ctx.messages.append(_assistant_message(resp))
    if _run_tool_calls(ctx, resp.tool_calls):
        return nudges, EXIT_FINISHED
    return nudges, None


def _work_loop(ctx: _Work, max_steps: int) -> str:
    """Run the bounded turn loop; return one of the three ``EXIT_*`` constants.

    Each turn: poll the operator inbox, window the history to the context budget
    (if set), call ``complete`` through the bounded degradation ladder, account
    the turn, then either run its tool calls or handle a no-tool turn.

    The budget counts *successful model turns*, not raw iterations, and nothing
    in this function adds to it. There are exactly three ``return`` statements
    and no ``raise``: whatever the injected ``complete`` raises propagates to
    :func:`run`, which preserves the partial — that is the host's failure
    surfacing, not a fourth way for the loop to decide it is done.
    """
    nudges = 0
    budget = max(1, max_steps)
    while ctx.result.stats.model_turns < budget:
        _drain_operator_inbox(ctx)
        resp = _complete_with_degradation(ctx)
        _account_turn(ctx, resp)
        nudges, exit_reason = _advance_turn(ctx, resp, nudges)
        if exit_reason == EXIT_FINISHED:
            return EXIT_FINISHED
        if exit_reason == EXIT_STOPPED:
            return EXIT_STOPPED
    return EXIT_BUDGET


# ── terminal shaping ──────────────────────────────────────────────────────────


def _finalize_stats(ctx: _Work, *, started_at: str, duration_seconds: float, model: str) -> None:
    """Fill the work-item-level stats known only at loop exit.

    Called on EVERY exit path — finish, stop, budget, and the aborted path — so
    even a partial drive carries populated stats. The executor's ledger
    attributes are optional: a minimal stand-in reports zero rather than raising.
    """
    result = ctx.result
    stats = result.stats
    stats.request = ctx.task.instruction
    stats.engine = ctx.task.engine
    stats.model = model
    stats.started_at = started_at
    stats.duration_seconds = duration_seconds
    stats.step_count = len(result.steps)
    stats.tool_counts = dict(Counter(step.tool for step in result.steps))
    stats.files_changed = len(result.changed_files)
    stats.bytes_written = _executor_int(ctx, "bytes_written")


def _executor_int(ctx: _Work, name: str) -> int:
    """Read an optional integer ledger attribute; 0 when absent or unusable."""
    try:
        return int(getattr(ctx.executor, name, 0) or 0)
    except (TypeError, ValueError) as exc:
        _degrade(ctx, DEGRADED_PROGRESS, f"executor.{name} unreadable: {exc}")
        return 0


def _snapshot_executor_ledger(ctx: _Work) -> None:
    """Copy the executor's optional ledgers onto the result, on every exit path."""
    changed = getattr(ctx.executor, "changed", None)
    if changed:
        try:
            ctx.result.changed_files = sorted(str(path) for path in changed)
        except TypeError as exc:
            _degrade(ctx, DEGRADED_PROGRESS, f"executor.changed unreadable: {exc}")
    sub_results = getattr(ctx.executor, "sub_results", None)
    if sub_results:
        ctx.result.sub_results = list(sub_results)


def _maybe_force_synthesis(ctx: _Work, outcome: str) -> None:
    """Force ONE no-tools synthesis turn when a context-rich run has no summary.

    Fires on any exit, guarded on an empty summary (an answered run is never
    touched), on at least one step having happened (nothing read, nothing to
    synthesise), and on a model turn still being left in the drive's budget.
    This is the most expensive failure mode — a full token spend with zero
    output — turned into a usable partial.

    The turn is *inside* ``max_steps``: :func:`run` holds one turn back from the
    reading budget when synthesis is armed, and the budget check below is what
    makes that reservation binding rather than advisory. A degenerate budget
    (one turn, all of it spent reading) simply gets no synthesis.

    Best-effort: a failure leaves the summary untouched, records a degradation,
    and lets the caller fall through to the last-substantive rung.
    """
    if not ctx.controls.synthesis:
        return
    if ctx.result.summary or ctx.result.stats.step_count <= 0:
        return
    if ctx.result.stats.model_turns >= ctx.turn_budget:
        return
    prompt = _EMPTY_FINISH_PROMPT if outcome == EXIT_FINISHED else _SYNTHESIS_PROMPT
    ctx.messages.append({"role": "user", "content": prompt})
    try:
        resp = _complete_with_degradation(ctx, phase=_PHASE_SYNTHESIZING)
    except Exception as exc:  # noqa: BLE001 - a finalize-time turn never raises
        _degrade(ctx, DEGRADED_SYNTHESIS, f"{type(exc).__name__}: {exc}")
        return
    _account_turn(ctx, resp)
    content = (resp.content or "").strip()
    if content:
        ctx.result.summary = content


def _resolve_terminal_summary(ctx: _Work, outcome: str) -> None:
    """Resolve the summary on an exit that produced none.

    Precedence: a real finish summary, then ONE forced synthesis turn, then the
    last substantive prose the model produced across the whole drive, then the
    :data:`~embodiment.contract.NO_RESULT_PRODUCED` sentinel — a machine-readable
    marker, so a caller can tell "no result" from a short one.
    """
    _maybe_force_synthesis(ctx, outcome)
    if not ctx.result.summary:
        ctx.result.summary = ctx.last_substantive or NO_RESULT_PRODUCED


def _apply_outcome_flags(ctx: _Work, outcome: str) -> None:
    """Map the exit reason onto the result's partial-state flags and status.

    ``not_finished`` is the budget case, ``stopped_without_finish`` the no-tool
    stop; they are orthogonal, and a clean finish leaves both False with status
    ``ok``. Any other exit is ``incomplete`` — honest status, never a bare ``ok``
    on a run that did not deliver.
    """
    result = ctx.result
    result.not_finished = outcome == EXIT_BUDGET
    result.stopped_without_finish = outcome == EXIT_STOPPED
    if outcome != EXIT_FINISHED:
        result.status = INCOMPLETE


# ── honest incompletion ───────────────────────────────────────────────────────

#: Substring markers indicating a summary merely *describes* a deliverable it
#: never produced, or admits it is unfinished. Matched case-insensitively.
_META_MARKERS: tuple[str, ...] = (
    "need to continue",
    "remaining work",
    "i have read",
    "i will ",
    "next i ",
    "to be implemented",
    "not yet implemented",
    "need to implement",
)

_REASON_ADVICE: dict[str, str] = {
    "write-no-changes": "re-scope or take over: the run finished without changing any files.",
    "empty-deliverable": (
        "re-run with a tighter scope or take over: the finish produced no usable deliverable."
    ),
    "budget-exhausted": (
        "split the task or raise max_steps: the run exhausted its budget before delivering."
    ),
    "no-progress-zero-steps": (
        "check the model's tool-calling support or escalate to another model: "
        "the run made zero tool calls."
    ),
}


def _is_meta(summary: str) -> bool:
    lower = summary.lower()
    return any(marker in lower for marker in _META_MARKERS)


def _classify_incompletion(
    *,
    outcome: str,
    write_intent: bool,
    changed_files: int,
    summary: str,
    step_count: int,
) -> Optional[IncompletionRecord]:
    """Classify a finished work item as complete, or say honestly why it is not.

    Pure and deterministic. Returns ``None`` when a deliverable is present:
    for a write-intent task any file change counts (even a wrong one is not an
    *absence*), and a clean finish reporting a substantive non-meta result is a
    legitimate "no change needed"; for a read-intent task the summary IS the
    deliverable, regardless of how the loop exited — a forced-synthesis answer
    at budget exhaustion still delivered.
    """
    stripped = summary.strip()
    substantive = bool(stripped) and stripped != NO_RESULT_PRODUCED and not _is_meta(stripped)

    if write_intent:
        if changed_files >= 1:
            return None
        if outcome == EXIT_FINISHED and substantive:
            return None
    elif substantive:
        return None

    if step_count == 0:
        reason = "no-progress-zero-steps"
    elif outcome == EXIT_BUDGET:
        reason = "budget-exhausted"
    elif write_intent and changed_files == 0:
        reason = "write-no-changes"
    else:
        reason = "empty-deliverable"

    return IncompletionRecord(
        reason=reason,
        evidence=(
            f"exit={outcome!r} with {changed_files} changed file(s) over {step_count} step(s)"
        ),
        recommendation=_REASON_ADVICE[reason],
    )


def _maybe_flag_incompletion(ctx: _Work, outcome: str) -> None:
    """Attach an incompletion record when the run produced no deliverable."""
    if not ctx.controls.incompletion:
        return
    record = _classify_incompletion(
        outcome=outcome,
        write_intent=ctx.controls.write_intent,
        changed_files=len(ctx.result.changed_files),
        summary=ctx.result.summary or "",
        step_count=ctx.result.stats.step_count,
    )
    if record is None:
        return
    ctx.result.incompletion = record
    if ctx.result.status == OK:
        ctx.result.status = INCOMPLETE


# ── the public entry point ────────────────────────────────────────────────────


def run(
    complete: CompleteFn,
    task: Task,
    *,
    executor: ToolExecutor,
    max_steps: int,
    system_prompt: Optional[str] = None,
    hooks: Optional[HookFn] = None,
    progress: Optional[ProgressFn] = None,
    observer: Optional[ObserverFn] = None,
    presence: Optional[PresenceSink] = None,
    operator_inbox: Optional[OperatorInboxFn] = None,
    continuity: Optional[ContinuityFn] = None,
    controls: Optional[LoopControls] = None,
    model: str = "",
    continued_from: Optional[str] = None,
) -> LoopOutcome:
    """Drive ``complete`` against ``task`` until finish, a prose stop, or the budget.

    Args:
        complete: the model seam — one call performs one model turn.
        task: the work item. Its ``context_packet`` (when present) is what the
            presence sink acknowledges.
        executor: the tool surface, injected. There is no default: what the
            model may do belongs to the host, always.
        max_steps: the model-turn budget, and the hard ceiling on how many times
            ``complete`` is called. Nothing extends it: not a hook, not a
            finish nudge, not the forced synthesis turn (which is reserved out
            of the budget, never added to it). A degradable-error retry is the
            one thing that does not *count* against it, because a retried turn
            is the same turn — and that ladder is separately bounded.
        system_prompt: the first system message. The built-in default names no
            tool, because this loop may not assume a tool surface exists.
        hooks: the injected hook runner (see :data:`HookFn`). ``None`` means the
            lifecycle fires nothing and the drive is identical to a hook-free one.
        progress: per-step sink (see :data:`ProgressFn`).
        observer: observability sink (see :data:`ObserverFn`).
        presence: the optional presence pump (see :class:`PresenceSink`).
            ``None``, or a sink whose ``active`` is false, leaves the drive
            byte-identical to one with no presence at all.
        operator_inbox: polled once per turn boundary; each message is routed to
            the presence sink.
        continuity: the memory/coherence boundary seam (see
            :data:`ContinuityFn`) — observation only, filled in by task t14.
        controls: the loop's own knobs; every default is the strict no-op.
        model: the model id, recorded on the stats so the artifact is
            self-describing about which mind ran it.
        continued_from: the id of the work item this one continues, seeded onto
            the result **before the first step**. It has to arrive here rather
            than be set on the returned result, because the ``before-memory``
            continuity boundary fires *inside* this call — a host assigning it
            afterwards would always be too late, and the durable record would
            lose its ``supersedes`` edge. (colleague sets the same field from
            its CLI front after ``run`` returns, which is fine for the feedback
            lineage it reads back off an artifact, but not for a mid-run write.)

    Returns:
        A :class:`LoopOutcome` — the result, the exit reason, and the loop's own
        hook-firing and degradation ledgers.

    Raises:
        LoopAborted: when ``complete`` or the executor raised something the loop
            does not treat as a self-correcting step. The partial work is
            finalized onto the carried result first, so a host can persist a
            non-empty trace before surfacing the failure.
    """
    _controls = controls or LoopControls()
    result = TaskResult(task_id=task.id, status=OK, continued_from=continued_from)
    ctx = _Work(
        complete=complete,
        executor=executor,
        task=task,
        result=result,
        messages=[],
        controls=_controls,
        hooks=hooks,
        progress=progress,
        observer=observer,
        presence=presence,
        operator_inbox=operator_inbox,
        continuity=continuity,
    )
    ctx.messages = [
        {"role": "system", "content": system_prompt or _DEFAULT_SYSTEM},
        {"role": "user", "content": _build_initial_content(ctx)},
    ]

    _presence_arm(ctx)
    _presence_acknowledge(ctx)
    _fire_hooks(ctx, event=EVENT_TASK_START)  # observe-only: the return is discarded

    started_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    start_monotonic = time.monotonic()

    # The synthesis reserve: one turn held back so a summary-less exit can still
    # answer WITHOUT the drive making more than ``max_steps`` completions. A
    # single-turn budget keeps its one reading turn and simply never synthesises.
    ctx.turn_budget = max(1, max_steps)
    reading_budget = ctx.turn_budget
    if _controls.synthesis and reading_budget > 1:
        reading_budget -= 1

    aborted: Optional[Exception] = None
    # Not EXIT_BUDGET: if the seam raises, the loop never reached a budget
    # decision, and saying so would misreport the loop's own conduct.
    outcome = EXIT_ABORTED
    try:
        outcome = _work_loop(ctx, reading_budget)
    except Exception as exc:  # noqa: BLE001 - preserve partial work on any seam failure
        aborted = exc
        result.status = ERROR
        result.error = f"{type(exc).__name__}: {exc}"

    # finish — once, on every exit including the aborted path. Observe-only.
    _fire_hooks(ctx, event=EVENT_FINISH)
    _snapshot_executor_ledger(ctx)
    _finalize_stats(
        ctx,
        started_at=started_at,
        duration_seconds=round(time.monotonic() - start_monotonic, 6),
        model=model,
    )
    _observe(ctx, "exit", outcome, aborted=aborted is not None)

    if aborted is not None:
        result.summary = (
            result.summary
            or ctx.last_substantive
            or f"aborted after {len(result.steps)} step(s): {result.error}"
        )
        raise LoopAborted(
            LoopOutcome(
                result=result,
                exit_reason=outcome,
                hook_firings=ctx.firings,
                degradations=ctx.degradations,
            )
        ) from aborted

    _apply_outcome_flags(ctx, outcome)
    _boundary(ctx, BOUNDARY_COMPLETION)
    _resolve_terminal_summary(ctx, outcome)
    # Re-finalize the turn-derived stats: a synthesis turn happens after the
    # loop, so its token spend must still reach the artifact.
    ctx.result.stats.duration_seconds = round(time.monotonic() - start_monotonic, 6)
    _maybe_flag_incompletion(ctx, outcome)
    _boundary(ctx, BOUNDARY_MEMORY)

    return LoopOutcome(
        result=result,
        exit_reason=outcome,
        hook_firings=ctx.firings,
        degradations=ctx.degradations,
    )
