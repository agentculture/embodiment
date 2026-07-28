"""embodiment.lifecycle — the continuity checkpoints (task t14).

Issue #2 draws a strict ownership split: ``eidetic-cli`` owns **memory**
(recall, provenance, consolidation, ageing, forgetting); ``coherence-cli`` owns
**the relationship between memory and the present** (quality, meaning, signal,
investiture, frames); and **embodiment owns the lived sequence** — *when*
something is perceived, considered, acted on, remembered, or revisited.

:mod:`embodiment.continuity` already carries the *seam* to both subsystems
(``probe`` / ``recall`` / ``remember`` / ``assess``, every call degrading
honestly rather than raising). This module is the *policy* layered on top of
it: it composes those calls into the three named checkpoints
:mod:`embodiment.loop` already fires — :data:`~embodiment.loop.BOUNDARY_ACTION`
/ :data:`~embodiment.loop.BOUNDARY_COMPLETION` /
:data:`~embodiment.loop.BOUNDARY_MEMORY` — and answers, at each one, *whether*
to consult coherence and *whether* to write memory.

It stores nothing, scores nothing and embeds nothing itself. Every number it
consults (a coherence subdimension) and every id it threads (a recalled record)
is read from what ``continuity`` already returned — never computed here.

How a host injects it
----------------------
``embodiment.loop.ContinuityFn`` is ``Callable[[Boundary], Any]``, so an
instance of :class:`ContinuityLifecycle` IS one::

    from embodiment.lifecycle import LifecycleConfig, build_continuity_fn
    from embodiment.loop import run

    lifecycle = build_continuity_fn(
        LifecycleConfig(data_dir="/var/lib/myapp/memory", scope="myapp")
    )
    outcome = run(complete, task, executor=executor, max_steps=20,
                  continuity=lifecycle)
    for event in lifecycle.events:      # the C3 ledger — see below
        log(event.to_dict())

One instance is meant to be reused across many drives: its per-work-item state
is keyed by ``Task.id`` and released at that work item's ``before-memory``
boundary. A host builds one per configured eidetic/coherence pairing, not one
per drive.

Three checkpoints, three different jobs
----------------------------------------
* **before-action** — the *considered* beat. Recalls related prior memory (the
  ids become the durable record's ``links``) and runs one coherence assessment
  of the pending step. What counts as "consequential" is discussed below.
* **before-completion** — the *reviewed* beat. One coherence assessment of the
  work item as it stands, purely observational. It never touches memory.
* **before-memory** — the *remembered* beat, and the ONLY checkpoint that ever
  calls :func:`embodiment.continuity.remember`. Gated by
  :func:`select_for_memory`.

Two things these checkpoints deliberately do NOT do. They never inject recalled
memory into the model conversation — what the acting mind is *told* is host
policy, the same line :mod:`embodiment.loop` draws for operator messages; a
recall here serves the considered beat and the record's ``links``. And they
never fire on the aborted path: a drive that raises reaches neither
``before-completion`` nor ``before-memory`` (the loop's own design), so a
crashed work item is not remembered, and the state it accumulated is released
by the bound described below rather than lingering.

Which actions are "consequential"
-----------------------------------
The loop offers ``before-action`` once per tool call, which is the closest
thing it has to *continuous* — and issue #2 says to check coherence at
meaningful **boundaries**, not continuously. An assessment also dials an
embedding endpoint, so "every call" is a real cost, not a stylistic one.

Two knobs, with a deliberate division of knowledge:

* **The host decides which actions matter.** ``LifecycleConfig.consequential``
  takes a predicate over the :class:`~embodiment.loop.Boundary`. A kiosk knows
  that ``send_message`` is consequential and ``get_weather`` is not; this
  module cannot know that, and guessing from a tool name would be exactly the
  kind of inference this package refuses elsewhere. A raising predicate
  degrades to the built-in cadence and is recorded, never propagated.
* **embodiment decides the cadence when the host does not.** With no
  predicate, the default consults on the FIRST action of each work item and
  records every later one as observed-but-skipped;
  ``consider_every_action=True`` restores per-call consultation.

Remember selectively — the policy, stated once
------------------------------------------------
"Storing everything is not understanding what matters" (issue #2), so the
selection policy is an explicit, pure, independently-testable function —
:func:`select_for_memory` — not a TODO. It asks three questions in order:

1. **Was there anything to remember?** A structural veto, read from what the
   loop *already decided*: an ``error`` status, an
   :class:`~embodiment.contract.IncompletionRecord` (the loop's own
   honest-incompletion classifier saying no deliverable was produced), an empty
   summary, or the :data:`~embodiment.contract.NO_RESULT_PRODUCED` sentinel.
   This runs first and outranks everything: coherence can narrow what is
   stored, never widen it past "there was nothing to store". It also costs
   nothing, so a failed run never pays for an assessment.
2. **Does coherence think it matters?** Read from the ``meaning`` domain's
   ``subdimensions.consequence`` / ``subdimensions.future_constraint`` exactly
   as ``coherence-cli``'s README documents for its ``eidetic`` consumer: *"high
   on either -> store; low on both -> compress or drop rather than persist"*.
   That mapping is a mesh precedent this module composes, not one it invented.
3. **And if coherence could not answer?** (No coherence installed, embedder
   unreachable, assessment disabled.) Then a real deliverable is stored, and
   the reason token says ``no-coherence-signal`` so the record's provenance is
   honest about having been selected without that signal. The alternative —
   dropping everything when coherence is absent — would make memory hostage to
   a subsystem issue #2 explicitly says is independently optional.

Provenance: perception -> action -> durable record
----------------------------------------------------
The chain each durable record carries:

* ``added_by`` — the resolved identity of the consuming rig
  (:func:`embodiment.identity.resolve_identity`, the same seam the prompt
  framing uses; never a parallel persona source and never inferred from a
  model name), or a ``LifecycleConfig.added_by`` override.
* ``links`` — the ids recalled at this work item's ``before-action``: what the
  agent knew when it acted, bounded by ``max_links``.
* ``supersedes`` — the record id of the work item this one continues
  (``TaskResult.continued_from``), computed with :func:`record_id_for` so no
  lookup is needed to find it.
* ``metadata["request"]`` — the **verbatim** request. When a perception seam
  ran, that is ``ContextPacket.original``, the operator's own words, never
  ``interpretation`` (which is model output). See :func:`request_text`; the
  same text is what the ``before-action`` recall queries with, so the memory an
  action was informed by and the memory it produced hang off one root.
* ``created`` — when the work *began* (``WorkStats.started_at``), not when the
  write happened.

Permission stays separate from coherence
------------------------------------------
Coherence asks whether an action makes *sense*; whether it is *permitted*
belongs to the capability layer — the loop's hook seam and the host's own
executor. The loop enforces this structurally: it CALLS the continuity seam and
discards whatever comes back (see :data:`embodiment.loop.ContinuityFn`). This
module does not undo that, in two independent ways:

1. :meth:`ContinuityLifecycle.__call__` always returns ``None``, on every
   boundary including an unrecognised one; and the module never imports,
   constructs or so much as *names* the loop's approval vocabulary in code, so
   there is no path by which a verdict could become a decision. An AST guard in
   ``tests/test_lifecycle.py`` pins that.
2. The converse holds by the loop's own dataclass:
   :class:`~embodiment.loop.Boundary` carries no approval state at all, so a
   checkpoint cannot read a decision even if it wanted to. Both directions are
   additionally proven behaviourally, through the real
   :func:`~embodiment.loop.run`.

Degradation is host-visible, always (C3)
------------------------------------------
Every :class:`LifecycleEvent` — a recall, an assessment, a write, a skip, a
degradation forwarded from ``continuity`` — is appended to
:attr:`ContinuityLifecycle.events` UNCONDITIONALLY, before any attempt to
forward it to an optional ``on_event`` sink. A raising sink is caught, disables
itself for the rest of the instance's life (mirroring
:mod:`embodiment.loop`'s own observer handling), and its failure becomes one
more recorded event. Nothing here is a bare ``except: pass``, and no public
entry point raises into the host.

The ledger is a **bounded** local record for host inspection, not a new event
stream: embodiment deliberately does not claim to own a presence event stream
(see :mod:`embodiment.events`, which routes the loop's own activity onto
``events-cli``'s fabric). A host that wants these on a bus wires ``on_event``
to one.

Both subsystems are optional
------------------------------
The first boundary records the continuity **mode** —
:data:`~embodiment.continuity.FULL_CONTINUITY`,
:data:`~embodiment.continuity.PARTIAL_CONTINUITY` or
:data:`~embodiment.continuity.NO_CONTINUITY` — so a host sees up front what it
is actually getting. With both subsystems absent the drive's own outcome is
identical to one with no continuity seam injected at all; only the recorded
transitions differ. With ``data_dir`` unset, every eidetic call refuses before
touching the store (``continuity``'s trap #1) and says so — that is the
supported coherence-only configuration.
"""

from __future__ import annotations

import os
import tempfile
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Optional, Union

from embodiment import continuity
from embodiment.contract import ERROR, NO_RESULT_PRODUCED, Task, TaskResult
from embodiment.identity import resolve_identity
from embodiment.loop import BOUNDARY_ACTION, BOUNDARY_COMPLETION, BOUNDARY_MEMORY, Boundary

__all__ = [
    "DEFAULT_RECORD_TYPE",
    "DEFAULT_CONSEQUENCE_THRESHOLD",
    "DEFAULT_MAX_LINKS",
    "DEFAULT_MAX_EVENTS",
    "DEFAULT_MAX_TRACKED_TASKS",
    "RECORD_ID_PREFIX",
    "CHECKPOINT_MODE",
    "CHECKPOINT_RECALLED",
    "CHECKPOINT_ASSESSED",
    "CHECKPOINT_ASSESS_SKIPPED",
    "CHECKPOINT_REMEMBERED",
    "CHECKPOINT_REMEMBER_SKIPPED",
    "CHECKPOINT_DEGRADED",
    "CHECKPOINT_KINDS",
    "ConsequentialFn",
    "LifecycleSink",
    "LifecycleEvent",
    "LifecycleConfig",
    "ContinuityLifecycle",
    "build_continuity_fn",
    "record_id_for",
    "request_text",
    "select_for_memory",
]

_StrPath = Union[str, "os.PathLike[str]"]

#: eidetic's ``type`` vocabulary is free-form ("task, experiment, lesson" —
#: its ``docs/contract.md`` §1); one work item's durable record is a "task".
DEFAULT_RECORD_TYPE = "task"

#: The midpoint of coherence's own ``[0, 1]`` subdimension range. "High on
#: either -> store" is coherence-cli's documented mapping; where exactly "high"
#: starts is a policy call this module cannot independently verify, so it is
#: stated as a constant and overridable per :class:`LifecycleConfig`.
DEFAULT_CONSEQUENCE_THRESHOLD = 0.5

#: Cap on how many recalled ids one record's ``links`` carries. Bounds an
#: unbounded-growth risk on a long drive with many recalls; never a claim about
#: how many recalled records were "relevant" (relevance is eidetic's).
DEFAULT_MAX_LINKS = 10

#: Cap on :attr:`ContinuityLifecycle.events`. A resident agent runs for days;
#: an unbounded ledger is a leak. Oldest entries are dropped first and the
#: count of dropped entries stays readable, so the bound is never silent.
DEFAULT_MAX_EVENTS = 1000

#: Cap on how many work items may be tracked between their ``before-action``
#: and ``before-memory`` boundaries. A drive that raises never reaches
#: ``before-memory`` (the loop's own design), so without a bound an abandoned
#: work item's state would live forever.
DEFAULT_MAX_TRACKED_TASKS = 64

#: Prefix applied to a ``Task.id`` to build its durable-memory record id.
#: Stable and predictable so a continuation's ``supersedes`` can be computed
#: from ``TaskResult.continued_from`` without a lookup.
RECORD_ID_PREFIX = "embodiment-task-"

# --- the event vocabulary ----------------------------------------------------
#
# A caller branches on ``kind``, and on ``detail`` (a short, stable token from
# the sets documented on each constant) — never on free text. Structured extras
# live in ``data``.

#: The continuity mode, recorded once at the first boundary. ``detail`` is one
#: of ``continuity``'s mode tokens.
CHECKPOINT_MODE = "mode"
#: Prior memory was recalled. ``data``: ``count``, ``ids``.
CHECKPOINT_RECALLED = "recalled"
#: A coherence assessment ran. ``data``: ``domains``, ``unavailable``.
CHECKPOINT_ASSESSED = "assessed"
#: No assessment ran. ``detail``: ``already-considered`` | ``not-consequential``
#: | ``disabled`` | ``unchanged`` | ``not-memory-worthy``.
CHECKPOINT_ASSESS_SKIPPED = "assess-skipped"
#: A durable record was written. ``detail`` is the selection reason;
#: ``data``: ``record_id``, ``links``, ``supersedes``, ``added_by``.
CHECKPOINT_REMEMBERED = "remembered"
#: No record was written. ``detail`` is the selection reason from
#: :func:`select_for_memory`.
CHECKPOINT_REMEMBER_SKIPPED = "remember-skipped"
#: Something degraded. ``detail`` is a ``continuity.CODE_*`` token when the
#: degradation came from a subsystem, else one of this module's own:
#: ``internal-error`` | ``sink-failed`` | ``trace-lost`` |
#: ``consequential-fn-failed`` | ``links-truncated`` | ``compiled-from-lost``.
CHECKPOINT_DEGRADED = "degraded"

#: The complete, closed vocabulary. There is no other ``kind`` this module emits.
CHECKPOINT_KINDS = (
    CHECKPOINT_MODE,
    CHECKPOINT_RECALLED,
    CHECKPOINT_ASSESSED,
    CHECKPOINT_ASSESS_SKIPPED,
    CHECKPOINT_REMEMBERED,
    CHECKPOINT_REMEMBER_SKIPPED,
    CHECKPOINT_DEGRADED,
)

# Reasons this module's own faults are recorded under (see CHECKPOINT_DEGRADED).
_FAULT_INTERNAL = "internal-error"
_FAULT_SINK = "sink-failed"
_FAULT_TRACE_LOST = "trace-lost"
_FAULT_CONSEQUENTIAL = "consequential-fn-failed"
_FAULT_LINKS_TRUNCATED = "links-truncated"
_FAULT_COMPILED_FROM_LOST = "compiled-from-lost"

# ``detail`` tokens for a skipped assessment.
_SKIP_CONSIDERED = "already-considered"
_SKIP_NOT_CONSEQUENTIAL = "not-consequential"
_SKIP_DISABLED = "disabled"
_SKIP_UNCHANGED = "unchanged"
_SKIP_NOT_WORTH_REMEMBERING = "not-memory-worthy"

# Selection reasons returned by :func:`select_for_memory`.
_REASON_ERROR = "error-status"
_REASON_INCOMPLETE = "incomplete"
_REASON_NO_DELIVERABLE = "no-deliverable"
_REASON_HIGH = "high-consequence"
_REASON_LOW = "low-consequence"
_REASON_NO_SIGNAL = "no-coherence-signal"

#: A host-supplied predicate naming which actions are consequential enough to
#: consult continuity before. Takes the boundary (which carries ``tool`` and
#: ``arguments``); returns ``True`` to consult. Never raises into this module —
#: one that does is recorded and the built-in cadence takes over.
ConsequentialFn = Callable[[Boundary], bool]

#: An optional sink a host injects to observe checkpoint activity live. Never
#: required: every event lands on :attr:`ContinuityLifecycle.events` regardless
#: of whether a sink was supplied, or whether the one supplied raises.
LifecycleSink = Callable[["LifecycleEvent"], None]


def record_id_for(task_id: str) -> str:
    """The durable-memory record id a work item with *task_id* is stored under."""
    return f"{RECORD_ID_PREFIX}{task_id}"


def request_text(task: Task) -> str:
    """The verbatim request this work item answers.

    When a perception seam ran, that is ``ContextPacket.original`` — the
    operator's own words, returned byte-for-byte, never
    ``ContextPacket.interpretation`` (which is model output). Otherwise it is
    the task instruction. This is the root of the provenance chain: the text
    the ``before-action`` recall queries with, and the text stored as the
    durable record's ``metadata["request"]``.
    """
    packet = getattr(task, "context_packet", None)
    original = getattr(packet, "original", None) if packet is not None else None
    if isinstance(original, str) and original.strip():
        return original
    return task.instruction


# ---------------------------------------------------------------------------
# events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LifecycleEvent:
    """One thing a checkpoint did, or failed to do.

    ``boundary`` is one of the loop's three boundary names (or ``""`` for an
    event that belongs to the instance rather than a boundary, i.e. the opening
    :data:`CHECKPOINT_MODE` record). ``kind`` is one of :data:`CHECKPOINT_KINDS`
    and ``detail`` a short stable token documented on that constant; ``data``
    carries kind-specific detail as plain JSON-safe values.
    """

    boundary: str
    kind: str
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "boundary": self.boundary,
            "kind": self.kind,
            "detail": self.detail,
            "data": dict(self.data),
        }


# ---------------------------------------------------------------------------
# the selective-remembering policy — one pure function
# ---------------------------------------------------------------------------


def _structural_verdict(result: TaskResult) -> tuple[bool, str]:
    """Was there anything to remember at all? No subsystem required.

    Composes the loop's own conclusions rather than re-deriving them: an
    ``error`` status, an :class:`~embodiment.contract.IncompletionRecord` (the
    loop's honest-incompletion classifier already saying no deliverable was
    produced), an empty summary, or the ``NO_RESULT_PRODUCED`` sentinel.
    """
    if result.status == ERROR:
        return False, _REASON_ERROR
    if result.incompletion is not None:
        return False, _REASON_INCOMPLETE
    summary = (result.summary or "").strip()
    if not summary or summary == NO_RESULT_PRODUCED:
        return False, _REASON_NO_DELIVERABLE
    return True, _REASON_NO_SIGNAL


def _meaning_readings(assess_outcome: continuity.AssessOutcome) -> list[float]:
    """The two subdimensions coherence documents for memory gating, if present.

    Returns an empty list whenever the ``meaning`` domain did not run or did not
    answer in numbers — which is the caller's signal to fall back, never an
    invitation to compute a substitute here.
    """
    meaning = assess_outcome.domains.get("meaning")
    if not isinstance(meaning, Mapping):
        return []
    subdimensions = meaning.get("subdimensions")
    if not isinstance(subdimensions, Mapping):
        return []
    return [
        float(value)
        for key in ("consequence", "future_constraint")
        for value in (subdimensions.get(key),)
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]


def select_for_memory(
    result: TaskResult,
    assess_outcome: Optional[continuity.AssessOutcome],
    *,
    threshold: float = DEFAULT_CONSEQUENCE_THRESHOLD,
) -> tuple[bool, str]:
    """Decide whether *result* is worth a durable memory write.

    Pure and deterministic: no I/O, no store, no scoring of its own. See the
    module docstring's "Remember selectively" section for the three rungs and
    why they are ordered this way. Called twice per ``before-memory`` boundary —
    once with ``assess_outcome=None`` as the cheap structural pre-check that
    decides whether an assessment is worth paying for, then again with the
    verdict once there is one.

    Returns:
        ``(should_remember, reason)``, where ``reason`` is a short, stable,
        machine-branchable token: ``error-status``, ``incomplete``,
        ``no-deliverable``, ``high-consequence``, ``low-consequence`` or
        ``no-coherence-signal``.
    """
    keep, reason = _structural_verdict(result)
    if not keep:
        return False, reason

    if assess_outcome is not None and assess_outcome.ok:
        readings = _meaning_readings(assess_outcome)
        if readings:
            if any(value >= threshold for value in readings):
                return True, _REASON_HIGH
            return False, _REASON_LOW

    return True, _REASON_NO_SIGNAL


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LifecycleConfig:
    """How the checkpoint policy reaches eidetic and coherence.

    Every field has a working default; the one a host almost always sets is
    ``data_dir``. Leaving it ``None`` is legitimate and supported — it is the
    coherence-only configuration — but it is never silent: every eidetic call
    then refuses before touching the store and records
    :data:`~embodiment.continuity.CODE_NO_STORAGE_ANCHOR` (``continuity``'s
    trap #1).
    """

    data_dir: Optional[_StrPath] = None
    """The memory store anchor, threaded straight to ``continuity``. It is the
    SOLE anchor in-process: without it eidetic would resolve a public record
    against whatever git repo the host process happens to be running in."""

    scope: str = continuity.DEFAULT_SCOPE
    visibility: str = continuity.DEFAULT_VISIBILITY
    record_type: str = DEFAULT_RECORD_TYPE

    added_by: Optional[str] = None
    """Overrides the identity stamped on a write. ``None`` (the default)
    resolves it from ``Task.repo_path`` through
    :func:`embodiment.identity.resolve_identity` — the same resolved-identity
    seam the prompt framing uses, never a parallel persona source."""

    consequential: Optional[ConsequentialFn] = None
    """Host predicate naming which actions are consequential. When set it fully
    replaces the built-in cadence below."""
    consider_every_action: bool = False
    """The built-in cadence when no predicate is supplied. ``False`` (default)
    consults on the first action of each work item only."""

    assess_action: bool = True
    """Whether ``before-action`` assesses. Recall still runs either way — a
    recall is a read, not the expensive half."""
    assess_completion: bool = True
    assess_memory: bool = True
    """Whether ``before-memory`` assesses. Disabling it does not disable memory
    writes; it removes coherence's signal from :func:`select_for_memory`, which
    then selects on the structural rung alone."""

    consequence_threshold: float = DEFAULT_CONSEQUENCE_THRESHOLD
    max_links: int = DEFAULT_MAX_LINKS
    max_events: int = DEFAULT_MAX_EVENTS
    max_tracked_tasks: int = DEFAULT_MAX_TRACKED_TASKS

    recall_top_k: int = continuity.DEFAULT_TOP_K
    recall_mode: str = continuity.DEFAULT_MODE
    """eidetic's own default ranking mode, mirrored rather than re-chosen —
    picking a different one here would be embodiment making a ranking decision
    it does not own. Note that the default (``hybrid``) dials the embedding
    endpoint; a host with no embedder sets ``"keyword"`` or ``"exact"``, both
    fully offline."""

    embed_fn: Optional[continuity.EmbedSeam] = None
    """coherence's own embedder injection point, threaded through untouched.
    ``None`` uses coherence's real HTTP embedder."""
    reference_date: Optional[date] = None
    """Date coherence's quality domain measures freshness against. ``None``
    lets ``continuity`` supply today's."""

    workdir: Optional[_StrPath] = None
    """Directory the assessment snapshots are written to (and removed from
    immediately after each call — ``coherence.assess`` reads an artifact from
    disk). ``None`` uses the platform temp directory; a host or test pins this
    to keep every artifact inside one throwaway tree."""


# ---------------------------------------------------------------------------
# per-work-item state — what before-memory needs from the earlier boundaries
# ---------------------------------------------------------------------------


@dataclass
class _Trace:
    """One work item's continuity state, keyed by ``Task.id``.

    Created at whichever boundary of that work item fires first and released at
    its ``before-memory`` — one lived sequence, closed exactly once.
    """

    considered: bool = False
    recalled_ids: list[str] = field(default_factory=list)
    compiled_from: list[str] = field(default_factory=list)
    assessed_text: Optional[str] = None
    assess_outcome: Optional[continuity.AssessOutcome] = None


# ---------------------------------------------------------------------------
# the text snapshots handed to coherence
# ---------------------------------------------------------------------------


def _action_snapshot(boundary: Boundary) -> str:
    lines = [f"task: {request_text(boundary.task)}"]
    if boundary.tool:
        lines.append(f"pending action: {boundary.tool} {boundary.arguments or {}}")
    return "\n".join(lines)


def _result_snapshot(boundary: Boundary) -> str:
    result = boundary.result
    lines = [f"task: {request_text(boundary.task)}"]
    if result.summary:
        lines.append(f"result: {result.summary}")
    if result.changed_files:
        lines.append(f"changed files: {', '.join(result.changed_files)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# the checkpoint policy
# ---------------------------------------------------------------------------


class ContinuityLifecycle:
    """A stateful :data:`~embodiment.loop.ContinuityFn`.

    Directly injectable as ``run(..., continuity=<instance>)``. Safe to reuse
    across many drives. See the module docstring for the checkpoint semantics,
    the selection policy, and the C3 guarantees.
    """

    def __init__(
        self,
        config: Optional[LifecycleConfig] = None,
        *,
        on_event: Optional[LifecycleSink] = None,
        muse: Any = None,
    ) -> None:
        self.config = config or LifecycleConfig()
        self._on_event = on_event
        # OPTIONAL provenance source: anything exposing ``compiled_from`` — in
        # practice :class:`embodiment.muse_runner.ThreadedMuseRunner`. Read
        # duck-typed and never imported, so this module keeps its import
        # posture and a host with no muse is unaffected.
        self._muse = muse
        self._sink_failed = False
        self._events: deque[LifecycleEvent] = deque(maxlen=max(1, self.config.max_events))
        self._dropped = 0
        self._traces: dict[str, _Trace] = {}
        self._status: Optional[continuity.ContinuityStatus] = None

    # -- what a host reads ---------------------------------------------------

    @property
    def events(self) -> tuple[LifecycleEvent, ...]:
        """Every recorded event still held, oldest first (bounded — see
        :attr:`dropped_events`). Populated unconditionally: this is the C3
        record a host reads even having supplied no sink, or a broken one."""
        return tuple(self._events)

    @property
    def dropped_events(self) -> int:
        """How many events the bounded ledger has discarded. Non-zero means
        older history is gone — visible, never silent."""
        return self._dropped

    @property
    def tracked_tasks(self) -> int:
        """How many work items are currently mid-sequence (between their first
        boundary and ``before-memory``)."""
        return len(self._traces)

    @property
    def status(self) -> Optional[continuity.ContinuityStatus]:
        """The continuity availability read taken at the first boundary, or
        ``None`` before any boundary has fired."""
        return self._status

    # -- the ContinuityFn entry point ---------------------------------------

    def __call__(self, boundary: Boundary) -> None:
        """Dispatch *boundary* to its checkpoint. Never raises into the host.

        The return value is always ``None`` — stated explicitly rather than
        left as a fall-through, because a future non-``None`` return here must
        never be mistakable for a control signal. The loop discards it either
        way; that discard is what keeps coherence out of the permission path.

        ``KeyboardInterrupt``/``SystemExit`` are the deliberate exception,
        exactly as in :mod:`embodiment.continuity`: never raise means never
        raise *errors*, not never yield control.
        """
        try:
            self._ensure_status()
            if boundary.name == BOUNDARY_ACTION:
                self._on_before_action(boundary)
            elif boundary.name == BOUNDARY_COMPLETION:
                self._on_before_completion(boundary)
            elif boundary.name == BOUNDARY_MEMORY:
                self._on_before_memory(boundary)
            # An unrecognised boundary is observed and ignored rather than
            # raised: LOOP_BOUNDARIES is the loop's closed vocabulary today, and
            # staying permissive lets this policy keep working if the loop ever
            # adds a fourth without a lock-step release of this module.
        except Exception as exc:  # noqa: BLE001 - never raise into the host
            self._emit(
                getattr(boundary, "name", ""),
                CHECKPOINT_DEGRADED,
                _FAULT_INTERNAL,
                error=f"{type(exc).__name__}: {exc}",
            )
        return None

    # -- the opening availability read --------------------------------------

    def _ensure_status(self) -> None:
        """Record the continuity mode once, at the first boundary.

        ``probe`` does no work and touches no store; it reports whether each
        subsystem imported. Recording it up front is what makes "degrades to a
        recorded no-continuity mode" true of the instance rather than only
        inferable from a pile of per-call failures.
        """
        if self._status is not None:
            return
        status = continuity.probe()
        self._status = status
        self._emit(
            "",
            CHECKPOINT_MODE,
            status.mode,
            eidetic=status.eidetic_available,
            coherence=status.coherence_available,
        )
        for degradation in status.degradations:
            self._emit_degradation("", degradation)

    # -- before-action: the considered beat ----------------------------------

    def _on_before_action(self, boundary: Boundary) -> None:
        trace = self._trace(boundary)
        consult, skip_reason = self._is_consequential(boundary, trace)
        if not consult:
            self._emit(BOUNDARY_ACTION, CHECKPOINT_ASSESS_SKIPPED, skip_reason)
            return
        trace.considered = True

        self._recall(boundary, trace)

        if not self.config.assess_action:
            self._emit(BOUNDARY_ACTION, CHECKPOINT_ASSESS_SKIPPED, _SKIP_DISABLED)
            return
        self._assess_and_emit(BOUNDARY_ACTION, _action_snapshot(boundary), trace)

    def _is_consequential(self, boundary: Boundary, trace: _Trace) -> tuple[bool, str]:
        """Whether to consult continuity before this action, and why not if not."""
        predicate = self.config.consequential
        if predicate is not None:
            try:
                return bool(predicate(boundary)), _SKIP_NOT_CONSEQUENTIAL
            except Exception as exc:  # noqa: BLE001 - a host predicate never breaks a checkpoint
                self._emit(
                    BOUNDARY_ACTION,
                    CHECKPOINT_DEGRADED,
                    _FAULT_CONSEQUENTIAL,
                    error=f"{type(exc).__name__}: {exc}",
                )
        if self.config.consider_every_action or not trace.considered:
            return True, ""
        return False, _SKIP_CONSIDERED

    def _recall(self, boundary: Boundary, trace: _Trace) -> None:
        """Recall prior memory for this request; keep the ids as provenance."""
        outcome = continuity.recall(
            request_text(boundary.task),
            data_dir=self.config.data_dir,
            scope=self.config.scope,
            visibility=self.config.visibility,
            top_k=self.config.recall_top_k,
            mode=self.config.recall_mode,
        )
        if outcome.ok:
            found = [
                str(record["id"])
                for record in outcome.records
                if isinstance(record, Mapping) and record.get("id")
            ]
            merged = trace.recalled_ids + [rid for rid in found if rid not in trace.recalled_ids]
            trace.recalled_ids = merged[: max(0, self.config.max_links)]
            self._emit(
                BOUNDARY_ACTION,
                CHECKPOINT_RECALLED,
                "",
                count=len(found),
                ids=list(found),
            )
        if outcome.degradation is not None:
            self._emit_degradation(BOUNDARY_ACTION, outcome.degradation)

    # -- before-completion: the reviewed beat --------------------------------

    def _on_before_completion(self, boundary: Boundary) -> None:
        if not self.config.assess_completion:
            self._emit(BOUNDARY_COMPLETION, CHECKPOINT_ASSESS_SKIPPED, _SKIP_DISABLED)
            return
        trace = self._trace(boundary)
        self._assess_and_emit(BOUNDARY_COMPLETION, _result_snapshot(boundary), trace)

    # -- before-memory: the remembered beat (the only write site) ------------

    def _on_before_memory(self, boundary: Boundary) -> None:
        trace = self._traces.pop(boundary.task.id, _Trace())
        self._gather_compiled_from(trace)

        # The cheap veto first: a run with nothing to remember never pays for
        # an assessment, and no verdict could overturn it anyway.
        keep, reason = select_for_memory(boundary.result, None)
        if not keep:
            self._emit(BOUNDARY_MEMORY, CHECKPOINT_ASSESS_SKIPPED, _SKIP_NOT_WORTH_REMEMBERING)
            self._emit(BOUNDARY_MEMORY, CHECKPOINT_REMEMBER_SKIPPED, reason)
            return

        assess_outcome: Optional[continuity.AssessOutcome] = None
        if self.config.assess_memory:
            assess_outcome = self._assess_and_emit(
                BOUNDARY_MEMORY, _result_snapshot(boundary), trace
            )
        else:
            self._emit(BOUNDARY_MEMORY, CHECKPOINT_ASSESS_SKIPPED, _SKIP_DISABLED)

        keep, reason = select_for_memory(
            boundary.result, assess_outcome, threshold=self.config.consequence_threshold
        )
        if not keep:
            self._emit(BOUNDARY_MEMORY, CHECKPOINT_REMEMBER_SKIPPED, reason)
            return

        self._remember(boundary, trace, reason)

    def _remember(self, boundary: Boundary, trace: _Trace, reason: str) -> None:
        record = self._build_record(boundary, trace)
        added_by = self._added_by(boundary.task)
        outcome = continuity.remember(
            record,
            data_dir=self.config.data_dir,
            scope=self.config.scope,
            visibility=self.config.visibility,
            added_by=added_by,
        )
        if outcome.ok:
            self._emit(
                BOUNDARY_MEMORY,
                CHECKPOINT_REMEMBERED,
                reason,
                record_id=outcome.record_id,
                links=list(record.get("links", [])),
                supersedes=record.get("supersedes"),
                added_by=added_by,
            )
        if outcome.degradation is not None:
            self._emit_degradation(BOUNDARY_MEMORY, outcome.degradation)

    # -- provenance: perception -> action -> durable record ------------------

    def _added_by(self, task: Task) -> Optional[str]:
        """The configured identity, or the consuming rig's resolved one.

        A blank ``repo_path`` is treated as "no rig to resolve" rather than
        handed to the resolver, which would read the ambient working directory
        — the same class of cwd-dependence ``continuity``'s trap #1 refuses.
        """
        if self.config.added_by is not None:
            return self.config.added_by
        repo_path = getattr(task, "repo_path", "") or ""
        if not str(repo_path).strip():
            return None
        return resolve_identity(repo_path)

    def _build_record(self, boundary: Boundary, trace: _Trace) -> dict[str, Any]:
        task = boundary.task
        result = boundary.result
        record: dict[str, Any] = {
            "id": record_id_for(task.id),
            "text": result.summary,
            "type": self.config.record_type,
            "metadata": self._metadata(task, result),
        }
        # Merge compiled-from ids (provenance: what the muse cited) and
        # lifecycle recall ids. compiled_from takes priority.
        all_ids: list[str] = []
        seen: set[str] = set()
        for rid in trace.compiled_from + trace.recalled_ids:
            if rid not in seen:
                seen.add(rid)
                all_ids.append(rid)
        if all_ids:
            max_links = max(0, self.config.max_links)
            if len(all_ids) > max_links:
                self._emit(
                    BOUNDARY_MEMORY,
                    CHECKPOINT_DEGRADED,
                    _FAULT_LINKS_TRUNCATED,
                    total=len(all_ids),
                    kept=max_links,
                )
                all_ids = all_ids[:max_links]
            record["links"] = list(all_ids)
        if result.continued_from:
            record["supersedes"] = record_id_for(result.continued_from)
        if result.stats.started_at:
            # When perception of THIS work item began, not when the write
            # happened — the honest provenance timestamp. Absent, eidetic
            # stamps its own.
            record["created"] = result.stats.started_at
        return record

    def _gather_compiled_from(self, trace: _Trace) -> None:
        """Pull the muse's citation surface onto *trace*, if a muse was wired.

        Read at the memory boundary rather than pushed by the host, because
        that is the only moment the full set is known: a muse thinks across
        several boundaries and cites more as it goes. Never raises — a
        provenance source that misbehaves costs links, and losing links must
        not cost the record they belong to.
        """
        if self._muse is None:
            return
        try:
            cited = tuple(getattr(self._muse, "compiled_from", ()) or ())
        except Exception as exc:  # noqa: BLE001 - provenance never aborts a write
            self._emit(
                BOUNDARY_MEMORY,
                CHECKPOINT_DEGRADED,
                _FAULT_COMPILED_FROM_LOST,
                error=f"{type(exc).__name__}: {exc}",
            )
            return
        for record_id in cited:
            text = str(record_id)
            if text and text not in trace.compiled_from:
                trace.compiled_from.append(text)

    def _metadata(self, task: Task, result: TaskResult) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "task_id": task.id,
            "status": result.status,
            "engine": result.stats.engine or task.engine,
            "model": result.stats.model,
            "step_count": result.stats.step_count,
            "changed_files": list(result.changed_files),
            "request": request_text(task),
        }
        packet = getattr(task, "context_packet", None)
        if packet is not None:
            # The perception fingerprint — what the seam made of the request.
            # Never ``interpretation``: the record's ``request`` above is the
            # verbatim text, and a reworded copy beside it would blur which one
            # was authoritative.
            metadata["perception"] = {
                "task_type": getattr(packet, "task_type", ""),
                "confidence": getattr(packet, "confidence", 0.0),
            }
        return metadata

    # -- the shared coherence plumbing ---------------------------------------

    def _assess_and_emit(
        self, boundary_name: str, text: str, trace: _Trace
    ) -> Optional[continuity.AssessOutcome]:
        """Assess *text*, reusing this work item's verdict when it is unchanged.

        ``before-completion`` and ``before-memory`` see the same work item
        moments apart. When the snapshot is byte-identical the verdict cannot
        have changed, so it is reused rather than re-derived — one embedding
        round-trip per work item state, not one per boundary — and the reuse is
        recorded rather than silent.
        """
        if text == trace.assessed_text and trace.assess_outcome is not None:
            self._emit(boundary_name, CHECKPOINT_ASSESS_SKIPPED, _SKIP_UNCHANGED)
            return trace.assess_outcome

        outcome = self._assess(boundary_name, text)
        if outcome is None:
            return None
        trace.assessed_text = text
        trace.assess_outcome = outcome
        if outcome.ok:
            self._emit(
                boundary_name,
                CHECKPOINT_ASSESSED,
                "",
                domains=sorted(outcome.domains),
                unavailable=sorted(outcome.unavailable),
            )
        if outcome.degradation is not None:
            self._emit_degradation(boundary_name, outcome.degradation)
        return outcome

    def _assess(self, boundary_name: str, text: str) -> Optional[continuity.AssessOutcome]:
        """Best-effort coherence assessment of *text*.

        ``coherence.assess`` reads an artifact from disk, so *text* is written
        to a throwaway file first and removed immediately afterwards.
        coherence's importability is checked BEFORE that write, so a host that
        never installed it pays no filesystem churn per boundary — the outcome
        would be the same degradation either way; this only skips a pointless
        write.
        """
        if not continuity.coherence_available():
            self._emit_degradation(
                boundary_name,
                continuity.Degradation(
                    subsystem="coherence",
                    stage="assess",
                    code=continuity.CODE_IMPORT_FAILED,
                    reason="coherence is not importable; assessment skipped",
                ),
            )
            return None

        snapshot: Optional[Path] = None
        try:
            handle, raw_path = tempfile.mkstemp(
                suffix=".md", prefix="embodiment-lifecycle-", dir=self.config.workdir
            )
            snapshot = Path(raw_path)
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(text)
            return continuity.assess(
                snapshot,
                embed_fn=self.config.embed_fn,
                reference_date=self.config.reference_date,
            )
        except OSError as exc:
            # The snapshot could not be produced, so there is no artifact for
            # coherence to read — the same failure class ``continuity`` records
            # when coherence cannot read one it was given.
            self._emit_degradation(
                boundary_name,
                continuity.Degradation(
                    subsystem="coherence",
                    stage="assess",
                    code=continuity.CODE_ARTIFACT_UNREADABLE,
                    reason=f"could not write the assessment snapshot: {exc}",
                    exception=type(exc).__name__,
                ),
            )
            return None
        finally:
            if snapshot is not None:
                try:
                    snapshot.unlink(missing_ok=True)
                except OSError as exc:  # pragma: no cover - defensive
                    self._emit(
                        boundary_name,
                        CHECKPOINT_DEGRADED,
                        _FAULT_INTERNAL,
                        error=f"snapshot cleanup failed: {type(exc).__name__}: {exc}",
                    )

    # -- per-work-item state -------------------------------------------------

    def _trace(self, boundary: Boundary) -> _Trace:
        """This work item's state, creating (and bounding) it as needed."""
        task_id = boundary.task.id
        trace = self._traces.get(task_id)
        if trace is not None:
            return trace
        limit = max(1, self.config.max_tracked_tasks)
        while len(self._traces) >= limit:
            # Oldest first. A work item only lingers here if its drive never
            # reached before-memory (an aborted run), and dropping it loses the
            # links it had gathered — so the loss is recorded, not silent.
            stale_id, _ = next(iter(self._traces.items()))
            del self._traces[stale_id]
            self._emit(
                boundary.name,
                CHECKPOINT_DEGRADED,
                _FAULT_TRACE_LOST,
                task_id=stale_id,
            )
        trace = _Trace()
        self._traces[task_id] = trace
        return trace

    # -- observability (C3) --------------------------------------------------

    def _emit_degradation(self, boundary_name: str, degradation: continuity.Degradation) -> None:
        self._emit(
            boundary_name,
            CHECKPOINT_DEGRADED,
            degradation.code,
            **degradation.to_dict(),
        )

    def _emit(self, boundary_name: str, kind: str, detail: str = "", **data: Any) -> None:
        """Record an event locally, then best-effort forward it to the sink.

        The local append happens unconditionally and first; the sink is always
        secondary. A raising sink is caught, disabled for the rest of this
        instance's life, and its own failure becomes one more recorded event —
        so nothing here is a silent ``except: pass``.
        """
        event = LifecycleEvent(boundary=boundary_name, kind=kind, detail=detail, data=dict(data))
        self._append(event)
        if self._on_event is None or self._sink_failed:
            return
        try:
            self._on_event(event)
        except Exception as exc:  # noqa: BLE001 - a sink never breaks a checkpoint
            self._sink_failed = True
            self._append(
                LifecycleEvent(
                    boundary=boundary_name,
                    kind=CHECKPOINT_DEGRADED,
                    detail=_FAULT_SINK,
                    data={"error": f"{type(exc).__name__}: {exc}"},
                )
            )

    def _append(self, event: LifecycleEvent) -> None:
        if self._events.maxlen is not None and len(self._events) == self._events.maxlen:
            self._dropped += 1
        self._events.append(event)


def build_continuity_fn(
    config: Optional[LifecycleConfig] = None,
    *,
    on_event: Optional[LifecycleSink] = None,
    muse: Any = None,
) -> ContinuityLifecycle:
    """Build a :data:`~embodiment.loop.ContinuityFn` ready to inject into ``run()``.

    A thin, discoverable alias for ``ContinuityLifecycle(config,
    on_event=on_event, muse=muse)`` — named to match
    :func:`embodiment.presence_engine.build_presence_executor`'s ``build_*``
    convention for the sibling injection point.

    ``muse`` is optional and duck-typed: anything exposing ``compiled_from``
    (a :class:`~embodiment.muse_runner.ThreadedMuseRunner` in practice) has its
    citation surface folded into the durable record's ``links``. ``None`` — the
    museless default — changes nothing.
    """
    return ContinuityLifecycle(config, on_event=on_event, muse=muse)
