"""The subagent seam — delegation whose bound is arithmetic, not convention.

A host that wants to decompose work injects one :data:`SubagentFn` into
:func:`embodiment.loop.run`. Its tool executor asks for a child by returning a
:class:`SpawnRequest` on a :class:`~embodiment.loop.ToolOutcome`; the loop
computes the child's *reach*, hands the seam a :class:`SubagentCall`, and
charges whatever the child spent back to the parent's own budget.

A child is an instance **similar to the parent** — the same drive shape, with
muse, scratchpad, memory and continuity all host-wireable at any depth. This
module forbids a child nothing *by kind*. What it bounds is reach, and it
bounds it two ways, independently:

Bound 1 — the spawn allowance (depth and subtree size)
-------------------------------------------------------
Every drive carries an integer allowance. A drive may spawn only while its
allowance is **strictly positive**, and a spawn does two things at once:

* the child is created with :func:`attenuate` of the parent's allowance — one
  less, floored at zero;
* the parent's own allowance is decremented by the *same* function.

So a drive with allowance ``a`` spawns at most ``a`` children, whose allowances
are ``a-1, a-2, … 0``, and each of those obeys the same rule. Two facts follow
by arithmetic alone, and they are what makes recursion terminate:

* **Depth is bounded by ``a``.** Every generation is strictly lower and zero
  cannot spawn, so no chain of ancestors is longer than ``a``.
* **The whole subtree is bounded**, not merely the next generation: the total
  number of descendants a drive with allowance ``a`` can ever create is
  ``T(a) = a + Σ T(i) for i < a`` = ``2**a - 1``, a finite number fixed before
  the first spawn happens.

This is deliberately provable the same way the loop's three exits are — by
reading the source, not by running it. ``tests/test_subagent.py`` pins it:
this module contains **exactly one arithmetic operation**, a subtraction of the
literal ``1`` inside :func:`attenuate`; it calls ``max`` nowhere and ``min``
only to *narrow*; it contains no augmented assignment; and
:class:`SubagentCall` — the only object that carries a child's reach — is
constructed in exactly one place, :func:`child_call`, from that decrement.
There is no code path in this module that can hand a child a number larger than
its parent's.

Bound 2 — the turn budget (total work)
---------------------------------------
``max_steps`` bounds the parent's model turns, and child turns draw from the
**same pool**: the loop charges every turn a child reports against the parent's
budget, so a subagent is not a fourth way to exceed a stated bound. A child is
offered only what the parent has left, and a request may ask for less
(:attr:`SpawnRequest.max_steps`) and can never ask for more.

The two bounds are independent on purpose. Even a host whose ``SubagentFn``
ignores the allowance it was handed still spends the parent's turns, so the
tree terminates regardless — the allowance bounds the *shape*, the budget
bounds the *work*.

What this seam does not do
--------------------------
* **It never builds a child's tool surface.** The parent's executor supplies
  the child's ``task`` and ``executor`` — already narrowed, if the host narrows
  them — and this module passes them through untouched. Tool-level boundaries
  (paths, approvals, which verbs exist at all) belong to the injected tool
  surface, which is the one layer that can actually enforce them. embodiment
  structurally has no shell and no filesystem (``tests/test_no_shell_host.py``)
  and must not pretend otherwise.
* **It cannot bind a host that ignores it.** A ``SubagentFn`` is arbitrary host
  code; nothing here stops it from calling :func:`embodiment.loop.run` with an
  allowance it invented, exactly as nothing stops a host from calling ``run``
  twice. The claim this module makes is narrower and true: *the seam offers no
  mechanism to widen a child's reach*, and the turn budget holds even when the
  allowance is ignored. Stating the boundary beats letting the name imply a
  sandbox.

Cheap and stdlib-only: this module imports nothing but :mod:`dataclasses`,
:mod:`typing` and :mod:`embodiment.contract`, which is what lets
:mod:`embodiment.loop` depend on it without loosening its own import pin.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from embodiment.contract import SubResult, Task

__all__ = [
    # the allowance
    "NO_SPAWNS",
    "as_count",
    "attenuate",
    # the seam
    "SpawnRequest",
    "SubagentCall",
    "SubagentResult",
    "SubagentFn",
    "child_call",
    # the parent's accounting
    "SPAWN_GRANTED",
    "SPAWN_REFUSED_ALLOWANCE",
    "SPAWN_REFUSED_BUDGET",
    "SPAWN_REFUSED_SEAM",
    "SPAWN_FAILED",
    "SPAWN_OUTCOMES",
    "SPAWN_REFUSALS",
    "SpawnRecord",
]


# ── the allowance ─────────────────────────────────────────────────────────────

#: An allowance of zero: this drive may not spawn. The floor of the decrement,
#: the default for every drive, and the value a host never has to think about.
NO_SPAWNS = 0


def as_count(value: Any) -> int:
    """Read an allowance from whatever a host handed over; never raise.

    The ONE place a number becomes an allowance without being attenuated first
    — the root's own declaration, the same act as declaring ``max_steps``. Every
    other allowance in a tree comes from :func:`attenuate` and is therefore
    strictly smaller than the one above it.

    A negative or unreadable value degrades to :data:`NO_SPAWNS`, which is the
    safe direction: a malformed allowance means *no delegation*, never
    unbounded delegation.
    """
    try:
        count = int(value)
    except (TypeError, ValueError):
        return NO_SPAWNS
    if count < NO_SPAWNS:
        return NO_SPAWNS
    return count


def attenuate(allowance: int) -> int:
    """One generation of decay: strictly one below the parent, floored at zero.

    **This function is the depth bound.** It is the only arithmetic in this
    module and the only producer of a child's allowance, so the sequence
    ``a, attenuate(a), attenuate(attenuate(a)), …`` strictly decreases until it
    reaches :data:`NO_SPAWNS`, where it is fixed — and a drive at
    :data:`NO_SPAWNS` cannot spawn. Recursion therefore terminates by
    arithmetic, in at most ``a`` generations, whatever any host does.

    It is applied twice per spawn: once to mint the child, once to decrement the
    parent that minted it. One function, one direction, no override.
    """
    below = as_count(allowance) - 1
    if below < NO_SPAWNS:
        return NO_SPAWNS
    return below


def _narrow(requested: Optional[int], ceiling: int) -> int:
    """Apply a host's request against a ceiling the loop computed.

    ``min``, never ``max``: a request may only ever lower what the parent
    already decided, so ``None`` (ask for nothing in particular) and a request
    above the ceiling land on the same value. This is the whole of "attenuation
    only, never widening" as executable code — there is no branch here that
    returns something larger than *ceiling*.
    """
    if requested is None:
        return ceiling
    return min(as_count(requested), ceiling)


# ── the seam's shapes ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SpawnRequest:
    """What a host's executor asks for when it wants to delegate.

    Returned on :attr:`embodiment.loop.ToolOutcome.spawn`. The host owns
    *what* the child is — its instruction, and the tool surface it may use.
    The loop owns *how far it reaches*.

    Fields
    ------
    task:
        The child work item, built by the parent's executor.
    executor:
        The child's tool surface, built by the parent's executor — narrowed
        already, if the host narrows it. Passed through untouched; this seam
        neither inspects nor wraps it.
    role:
        A label recorded on the resulting
        :class:`~embodiment.contract.SubResult` (e.g.
        :data:`embodiment.framing.ROLE_SUBAGENT`). Recorded only — it changes
        no authority, exactly as identity framing changes none.
    allowance:
        An OPTIONAL request to give the child *fewer* spawns than it would
        otherwise get. ``None`` means "the attenuated default". A value at or
        above the parent's own allowance is refused — not honoured, not an
        error — and the attenuated default is used instead; see
        :func:`child_call`.
    max_steps:
        An OPTIONAL request for a smaller turn budget than the parent has left.
        ``None`` means "whatever remains". Clamped down, never up.
    system_prompt / model:
        Passed through for the seam to use when it drives the child.
    context:
        Free-form host data carried to the seam untouched — how a host wires a
        child's muse, scratchpad or continuity without this module knowing what
        any of those are.
    """

    task: Task
    executor: Any
    role: Optional[str] = None
    allowance: Optional[int] = None
    max_steps: Optional[int] = None
    system_prompt: Optional[str] = None
    model: str = ""
    context: Optional[dict[str, Any]] = None


@dataclass(frozen=True)
class SubagentCall:
    """The attenuated child, as the loop hands it to the injected seam.

    Every bound on this object is COMPUTED from the parent's live state by
    :func:`child_call`; none of them can be raised by anything a host puts in a
    :class:`SpawnRequest`. A seam that drives the child with
    :func:`embodiment.loop.run` passes :attr:`allowance` straight through as
    ``spawn_allowance``, :attr:`lineage` as ``lineage``, and :attr:`max_steps`
    as ``max_steps`` — that is the whole protocol.

    Fields
    ------
    allowance:
        How many descendants this child may create, tree-wide. Strictly below
        the parent's, always.
    max_steps:
        The child's model-turn budget, drawn from what the parent has left.
        Turns the child reports spending are charged to the parent.
    lineage:
        Ancestor task ids, root first, ending with the immediate parent. Its
        length IS the child's depth, so nothing has to count generations
        separately — and a subagent tree is walkable from artifacts alone.
    """

    task: Task
    executor: Any
    allowance: int
    max_steps: int
    lineage: tuple[str, ...] = ()
    role: Optional[str] = None
    system_prompt: Optional[str] = None
    model: str = ""
    context: Optional[dict[str, Any]] = None

    @property
    def parent_task_id(self) -> str:
        """The immediate parent's task id, or ``""`` at the root."""
        return self.lineage[-1] if self.lineage else ""

    @property
    def depth(self) -> int:
        """How many ancestors this child has. Bounded by the root's allowance."""
        return len(self.lineage)


@dataclass
class SubagentResult:
    """What an injected :data:`SubagentFn` reports back for one child drive.

    Fields
    ------
    sub_result:
        The child's artifact, recorded on the parent's
        :attr:`~embodiment.contract.TaskResult.sub_results`. ``None`` is legal
        and means the child ran but produced no artifact — the turns are still
        charged, because they were still spent.
    model_turns:
        Model turns the child spent, INCLUDING its own descendants'. Charged
        against the parent's ``max_steps`` verbatim: a seam that overspends its
        grant is charged for the overspend (and the loop records it), because
        the alternative is a budget that quietly means less than it says.
    degradations:
        The child's own degradation records, carried up untouched. The loop
        parks them on :attr:`SpawnRecord.degradations`; folding them into the
        host's one degradation stream with child attribution is the ledger
        lane's job (task t10), not this module's.
    result:
        Optional text for the acting model to read as the delegating tool's
        result. Empty means the loop renders its own one-line note.
    exit_reason:
        The child's exit reason, recorded for the parent's accounting.
    """

    sub_result: Optional[SubResult] = None
    model_turns: int = 0
    degradations: list[Any] = field(default_factory=list)
    result: str = ""
    exit_reason: str = ""


#: The injected delegation seam: given one attenuated :class:`SubagentCall`,
#: drive the child and report what it cost. Returning ``None`` means the host
#: declined to run the child — legal, and recorded as
#: :data:`SPAWN_REFUSED_SEAM` rather than passed off as a child that ran.
SubagentFn = Callable[[SubagentCall], Optional[SubagentResult]]


def child_call(
    request: SpawnRequest,
    *,
    parent_allowance: int,
    parent_task_id: str,
    parent_lineage: tuple[str, ...],
    turns_available: int,
) -> SubagentCall:
    """Build the attenuated child. **The only producer of a** :class:`SubagentCall`.

    Both bounds are computed here, from the parent's state, and both can only
    shrink:

    * ``allowance = _narrow(request.allowance, attenuate(parent_allowance))``
      — the ceiling is already one below the parent, and a request can only
      lower it further. A request at or above the parent's own allowance is
      therefore refused in the only sense that matters: it never reaches the
      child.
    * ``max_steps = _narrow(request.max_steps, turns_available)`` — where
      *turns_available* is what the parent has left of its own ``max_steps``.

    Caller contract: *turns_available* must already be what the parent can
    afford. This function does not know the parent's budget; it only refuses to
    exceed the number it was handed.
    """
    return SubagentCall(
        task=request.task,
        executor=request.executor,
        allowance=_narrow(request.allowance, attenuate(parent_allowance)),
        max_steps=_narrow(request.max_steps, turns_available),
        lineage=(*parent_lineage, parent_task_id),
        role=request.role,
        system_prompt=request.system_prompt,
        model=request.model,
        context=request.context,
    )


# ── the parent's accounting ───────────────────────────────────────────────────

#: A child was minted and the seam ran it.
SPAWN_GRANTED = "granted"
#: The parent's allowance was spent. **The bound working, not a failure** — so
#: this records no degradation, for the same reason a ``budget`` exit records
#: none: a ledger that reports the design working claims breakage that did not
#: happen (see :mod:`embodiment.ledger`'s "degradation is not incompletion").
SPAWN_REFUSED_ALLOWANCE = "refused-allowance"
#: No model turns were left in the parent's budget to lend. Also the bound
#: working, and also not a degradation.
SPAWN_REFUSED_BUDGET = "refused-budget"
#: A spawn was requested and no seam ran it — either no ``SubagentFn`` was
#: injected, or the injected one returned ``None``. This one IS recorded as a
#: degradation: the acting model asked for help and silently got none.
SPAWN_REFUSED_SEAM = "refused-seam"
#: The injected seam raised, returned something unusable, or overspent its
#: grant. Recorded as a degradation.
SPAWN_FAILED = "failed"
#: Every way a spawn attempt can end.
SPAWN_OUTCOMES = (
    SPAWN_GRANTED,
    SPAWN_REFUSED_ALLOWANCE,
    SPAWN_REFUSED_BUDGET,
    SPAWN_REFUSED_SEAM,
    SPAWN_FAILED,
)
#: The outcomes in which no child ran.
SPAWN_REFUSALS = (
    SPAWN_REFUSED_ALLOWANCE,
    SPAWN_REFUSED_BUDGET,
    SPAWN_REFUSED_SEAM,
    SPAWN_FAILED,
)


@dataclass(frozen=True)
class SpawnRecord:
    """One spawn attempt, granted or refused — the parent's own accounting.

    Rides :attr:`embodiment.loop.LoopOutcome.spawns`, beside the hook firings
    and the degradation ledger, because it is the loop's record of its own
    conduct rather than part of the artifact shape every seam shares.

    ``allowance_requested`` is kept next to ``allowance_granted`` on purpose: a
    narrowing is visible in the record rather than inferable from its absence,
    so "the host asked for more than it got" is a thing an operator can read.
    """

    outcome: str
    tool: str = ""
    step_index: int = 0
    parent_task_id: str = ""
    child_task_id: Optional[str] = None
    role: Optional[str] = None
    allowance_requested: Optional[int] = None
    allowance_granted: Optional[int] = None
    steps_granted: Optional[int] = None
    model_turns: int = 0
    exit_reason: str = ""
    reason: str = ""
    degradations: tuple[Any, ...] = ()
    """The child's own degradation records, verbatim. Task t10's ledger lane
    reads these and attributes them to ``child_task_id``; nothing else does."""

    @property
    def granted(self) -> bool:
        return self.outcome == SPAWN_GRANTED

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready. Absent numbers are OMITTED, never emitted as zeros —
        ``allowance_granted`` of ``0`` is a real grant of no further spawns and
        must not read the same as "never granted".

        :attr:`degradations` is deliberately absent: those are another lane's
        records in another lane's shape, and serialising them is that lane's
        own ``to_dict``'s job, never this one's (the same rule
        :attr:`embodiment.ledger.LedgerRecord.original` follows)."""
        data: dict[str, Any] = {
            "outcome": self.outcome,
            "tool": self.tool,
            "step_index": self.step_index,
            "parent_task_id": self.parent_task_id,
            "model_turns": self.model_turns,
        }
        for name in (
            "child_task_id",
            "role",
            "allowance_requested",
            "allowance_granted",
            "steps_granted",
        ):
            value = getattr(self, name)
            if value is not None:
                data[name] = value
        for name in ("exit_reason", "reason"):
            value = getattr(self, name)
            if value:
                data[name] = value
        return data
