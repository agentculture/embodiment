"""Scope-governed composition over the actor loop — ``run()``, unmodified (task t4).

:mod:`embodiment.scope` is the strategist's reasoning and
:mod:`embodiment.strategist_runner` is its thread. This module is the third
piece and the only one the actor ever meets: it composes
:func:`embodiment.loop.run` **through the seams that function already exposes**,
so a host gains a strategic tier without the acting loop changing by one line.

The diff to ``loop.py`` from this work is zero
----------------------------------------------
That is an acceptance criterion, not an aspiration, and it is held three ways:

* :func:`run_scoped` calls :func:`embodiment.loop.run` **once**, passing only
  keywords ``run`` already declares. Everything a host would have handed ``run``
  is forwarded verbatim through ``**actor_kwargs``, so an unknown keyword is
  refused by ``run``'s own signature rather than absorbed here — this function
  structurally cannot invent a parameter.
* Exactly three of those seams are wrapped: ``complete`` (the turn boundary),
  ``progress`` (what the actor has actually done) and ``operator_inbox``
  (operator intent as a review trigger). Each wrapper delegates to the host's
  own object, and with the lane off **the host's object is passed straight
  through by identity** — not a copy, not a shim.
* ``loop.py`` names no scope identifier at all. ``tests/test_scoped_run.py``
  reads its AST to say so, and walks the actor's whole transitive import closure
  to prove no scope module is reachable from it.

Delivery is EVENT-SHAPED (confirmed decision ``c35``)
-----------------------------------------------------
A strategic decision does not rewrite the actor's system prompt. It **raises an
event that is inserted into the turn stream** at a safe boundary — one more
message, appended to the running history the same way the loop's own finish
nudge is. Two consequences, both load-bearing:

* The system prompt is never touched mid-drive. The identifier does not appear
  as code anywhere in this module; it rides ``**actor_kwargs`` untouched.
* The boundary is the ``complete`` call. A directive that finishes reviewing in
  the middle of a tool step cannot reach the actor until the next turn begins,
  because that is the only place this module ever appends.

Two consequences of *where* that boundary sits, stated rather than discovered:

* The loop's bounded degradation ladder can call ``complete`` more than once for
  one turn (a media-rejection flatten, or a context-overflow shrink-and-retry).
  Each of those is a real seam call and is counted as a boundary here. It cannot
  double-apply anything — ``drain`` hands each review over once, and the applied
  chain refuses a repeated ``scope_id`` or a version that does not advance — but
  a host reading ``counts["boundaries"]`` is reading seam calls, not model turns.
* A host running with a ``context_budget`` windows its history every turn, so an
  event inserted many turns ago can be windowed back out of the actor's context
  like any other old message. This layer does not re-assert scope to compensate
  and does not pretend the window is not there: :attr:`ScopedOutcome.active` and
  the transition record are the durable answer to what governed the drive, and
  a host that needs scope in front of the model at all times puts it in the
  system prompt it owns.

Rendering goes through framing composition, and through NOTHING else
--------------------------------------------------------------------
:mod:`embodiment.framing` already carries the exact discipline this needs — it
names *who is speaking, never what they may do*, and :func:`~embodiment.framing.
frame_cortex` targets the top-level acting loop only. Directive rendering
follows it rather than inventing a second prompt path (spec claim ``c30``,
honesty condition ``h22``):

* :func:`_scope_event` is the ONE function in this module that builds a message,
  and its content is ``frame_cortex(...)``. There is exactly one call to it here
  and ``tests/test_scoped_run.py`` pins both facts by AST.
* With **no configured identity**, ``frame_cortex`` hands its argument back by
  name, so the composed bytes are :func:`render_directive`'s output exactly —
  the absent-identity byte-identical rule from `colleague#352
  <https://github.com/agentculture/colleague/issues/352>`_, extended to this
  tier.
* ``muse=False`` is fixed, deliberately. Whether the rig runs an advisory lane
  belongs to the host's *system* prompt; a scope event that announced a second
  mind would be describing the rig, not the scope.

Apply ``drain``'s output, NEVER the register (embodiment#54)
-------------------------------------------------------------
:meth:`embodiment.scope.ScopeLoop.review` admits a directive to the
:class:`~embodiment.scope.ScopeRegister` on the worker thread, *before* the
runner decides whether it still reaches the actor. So
:attr:`~embodiment.strategist_runner.StrategistRunner.active_directive` can name
a directive this drive never received — one the runner legitimately withheld as
stale or superseded.

Nothing in this module reads that register. :attr:`ScopedOutcome.active` is what
was **applied here**, boundary by boundary, and it is the only honest answer to
"what is the actor working under?". Reporting an issued-but-withheld directive
as governing behaviour would be a strategic claim that never took effect, which
this package treats as worse than a crash (constraint C3).

One layer of that check is repeated here rather than trusted: a drained
directive whose ``version`` does not strictly advance on the **applied**
version, or whose ``scope_id`` was already applied, is withheld and recorded.
What is deliberately *not* re-checked is ``supersedes``: the runner's register
validated it against the complete **issued** chain, and re-validating against
the shorter *received* chain would refuse every directive that supersedes one
this lane withheld — stranding the actor under old scope forever, which is the
exact failure the check exists to prevent.

Ordinary tool steps are not strategic reports (issue #51)
----------------------------------------------------------
A review costs a dense thinking model minutes. Offering one per tool call would
be both wasteful and wrong: the strategist owns direction, not steps. So a
snapshot goes up only when something **material** changed, and materiality is
decided in two places, neither of them by guesswork here:

* This module builds a typed :class:`~embodiment.scope.ScopeReport` from what it
  can honestly observe of the actor — repeated tool failures, and operator
  intent that arrived at the inbox. It projects only when that report *differs
  from the last one projected* (plus the opening boundary, and plus a fixed
  cadence if the host asks for one).
* The host's ``projector`` then decides what the world means.
  :class:`ScopeSnapshot`'s objectives, commitments, resources and conflicts are
  domain facts embodiment cannot infer, so the projector is host-supplied and
  may return ``None`` for "nothing worth a review". A snapshot equal to the last
  one offered is dropped without a second offer.

``material_outcomes``, ``conflicts``, ``new_constraints`` and
``commitments_at_risk`` are left empty on the report on purpose. This module
watches a tool loop; it does not know what a commitment is, and filling those
fields with plausible-looking derivations would be exactly the "looks attentive,
is not" failure C3 exists to forbid.

Operator intent is a TRIGGER, not content (the parked t4 question)
-------------------------------------------------------------------
The spec parked whether ``operator_inbox`` should feed strategic review. It
does, minimally: a polled message makes the next boundary material and rides the
report's ``requested_decision``, where the host's projector decides whether it
belongs in a snapshot. The loop's own routing is untouched — the host's inbox
callable is still what ``run`` polls, its return value is passed back unchanged,
and an inbox that raises is still the loop's own ``presence-failed``
degradation, not this module's. Note the threat model (spec claim ``c29``):
operator text is untrusted, and letting it reach the strategist is the first
hop of the injection chain. It is surfaced to the *host*, never inserted into
the actor's context by this module.

Degrade, never raise
--------------------
Every call into host or strategist code is guarded and every guard records a
:class:`ScopeTransition` (constraint C3). A dead lane, an unstartable thread, a
hostile ``drain`` and a raising projector all leave the actor running under the
last valid directive — or the explicit host default scope — and the drive
completes. Nothing here degrades silently and nothing here raises into the
acting loop's main path.

**No new ``DEGRADED_``/``DROPPED_`` constant is minted in this module**, and
that is a decision rather than an omission: task t3 adds the ledger's scope lane
with *exactly one* ``_MODULES`` row pointed at
:mod:`embodiment.strategist_runner`, so a second vocabulary here would need a
second row. The lane's own degradations are relayed on
:attr:`ScopedOutcome.scope_degradations`; what this module adds is the
*application* record, which is a different question — what governed the actor,
and when.

Containment is at drive boundaries (v1)
----------------------------------------
Closing the runner stops new directives; the active directive governs until the
drive ends. There is no mid-drive directive-drop move, and this module does not
drain at drive end: handing back a directive the actor can no longer act on
would put a decision in the record that never governed anything.

Two persistence lanes, and the storage owner is the HOST's (task t13, ``c33``)
------------------------------------------------------------------------------
A drive runs in exactly one lane, and every record it writes names it:

* **durable** — the chain survives across drives and across a process restart.
  It crosses that boundary through :class:`ScopePersistence`, an injected port
  of two host callables. Which component stores the payload is *parked* by the
  frame (open vagueness ``v5``: host state round-tripped through the projector,
  or a continuity record), so nothing here chooses one — this module opens no
  file, imports no driver and reaches no memory subsystem.
* **session-scoped** — a :class:`ScopeSession` holds the chain inside one
  process and nowhere else. A session-governed drive holds **no port at all**
  (:class:`_Governed` drops it at construction), which is what makes "a session
  never writes the durable lane" structural rather than conditional. Sessions
  are the future seam for per-subagent scoping; per-subagent scoping is not
  built here.

A host that wires neither gets exactly today's behaviour: an anonymous
session-lane chain that lives for one drive, started from the explicit host
default scope, with nothing written anywhere.

What is persisted is what was **applied** (embodiment#54, again). The lane's
chain is fed from :meth:`_Governed._seat` — the same applied chain
:attr:`ScopedOutcome.active` reports — never from a strategist's register, so a
directive the actor never received cannot come back after a restart as the scope
it was working under. And because that chain legitimately has gaps, restoring it
is a replay rather than a proposal: see :meth:`embodiment.scope.
ScopeRegister.receive`.

Observability rides the host's OWN observer (task t5)
-------------------------------------------------------
This module imports no event fabric — :mod:`embodiment.scope_events` is a pure
translation layer, and the only thing wired here is the SAME ``observer=``
keyword a host already hands ``run_scoped`` for the actor's own
:class:`~embodiment.loop.LoopEvent` stream (``**actor_kwargs``, read but never
popped, so the actor's own events are unaffected). Every
:class:`ScopeTransition` this module records, every
snapshot and report it offers upward, every review it drains and every
lane-level degradation it relays is translated by
:mod:`embodiment.scope_events` and handed to that same observer — never a
second event stream, never a claim this package owns the fabric. A raising
observer is recorded once (mirroring :mod:`embodiment.loop`'s own
``_observe``) and then disabled for the rest of the drive: an observer must
never abort one (constraint C3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# Direct submodule import, NOT ``from embodiment import scope_events``: the latter
# names the package hub, whose lazy re-export surface pulls the whole closure in and
# trips the scope modules' stdlib-only import ban (tests/test_scope_authority.py).
import embodiment.scope_events as scope_events
from embodiment.contract import Task, TaskResult
from embodiment.framing import frame_cortex
from embodiment.loop import CompleteFn, LoopAborted, LoopOutcome, ToolExecutor, run
from embodiment.scope import (
    LANE_DURABLE,
    LANE_SCHEMA_VERSION,
    LANE_SESSION,
    SCOPE_EXIT_UNCHANGED,
    SCOPE_STATUS_ACTIVE,
    SCOPE_STATUS_BLOCKED,
    ScopeDirective,
    ScopeRegister,
    ScopeReport,
    ScopeSnapshot,
)

__all__ = [
    # the application vocabulary (deliberately NOT a DEGRADED_/DROPPED_ lane)
    "TRANSITION_DEFAULT",
    "TRANSITION_APPLIED",
    "TRANSITION_HELD",
    "TRANSITION_WITHHELD",
    "TRANSITION_DEGRADED",
    "TRANSITION_UNPROJECTED",
    "TRANSITION_KINDS",
    # shapes
    "ScopeContext",
    "ScopeProjectorFn",
    "ScopeTransition",
    "ScopedControls",
    "ScopeGovernor",
    "ScopedOutcome",
    # persistence lanes (c33)
    "ScopePersistence",
    "ScopeSession",
    # composition
    "render_directive",
    "run_scoped",
]


# ── the application vocabulary ────────────────────────────────────────────────
#
# These name what happened to the ACTOR's scope, which is a different question
# from the strategist lane's own degradations (those keep their ``strategist-``
# codes and are relayed untouched). Deliberately not prefixed ``DEGRADED_``
# or ``DROPPED_``: task t3's ledger harvest takes exactly one module, and a
# second vocabulary here would silently ask for a second row.

#: The explicit host default scope was seated and delivered. Never inferred and
#: never model-produced: absent a host default, the actor starts under no scope.
TRANSITION_DEFAULT = "scope-default-applied"
#: A directive was rendered and inserted at a boundary: one ``drain`` handed
#: over, or one **restored from a persistence lane** at the start of the drive
#: (task t13). Both are model-produced scope reaching the actor, which is what
#: separates them from :data:`TRANSITION_DEFAULT`; the record's ``lane`` and
#: ``reason`` say which.
TRANSITION_APPLIED = "scope-directive-applied"
#: The strategist wrote a hold — the current scope still fits. A real answer, and
#: a graded one: a lane that only counted directives would report a careful
#: strategist as an idle one.
TRANSITION_HELD = "scope-held"
#: Something reached this layer and was NOT applied: a review that produced no
#: directive, a version that does not advance, an id already applied, or an
#: outcome this layer could not read. Recorded, never dropped.
TRANSITION_WITHHELD = "scope-withheld"
#: A lane this layer depends on failed: the strategist lane stopped, or the
#: host's persistence port could not be read or written (task t13). The record
#: names which lane and what the actor continues under. A persistence failure
#: costs *durability*, never delivery — the directive still governs the drive —
#: and the record says so rather than letting the two be conflated.
TRANSITION_DEGRADED = "scope-lane-degraded"
#: The host's projector failed, or the snapshot could not be offered. No
#: snapshot went up; the actor is unaffected.
TRANSITION_UNPROJECTED = "scope-unprojected"
#: The complete set. There is no seventh.
TRANSITION_KINDS = (
    TRANSITION_DEFAULT,
    TRANSITION_APPLIED,
    TRANSITION_HELD,
    TRANSITION_WITHHELD,
    TRANSITION_DEGRADED,
    TRANSITION_UNPROJECTED,
)


# ── the inserted event ────────────────────────────────────────────────────────

#: Scope arrives as an ordinary conversational turn, exactly like the loop's own
#: finish nudge. NOT a system message: ``c35`` says the system prompt is never
#: rewritten mid-drive, and a mid-stream system turn is the same claim wearing a
#: different hat.
_EVENT_ROLE = "user"

_HEADER = "[scope directive — {scope_id}, version {version}]"

#: Rule 4 of the framing charter, restated for the one thing framing does not
#: cover: a directive's *prose*. The strategist may legitimately write
#: "run the full test suite before shipping" as a strategic statement, and the
#: surrender-direction failure is an actor reading that as an operational
#: instruction. So the event says out loud what it is — every time scope
#: changes, which is exactly when the reminder is worth its tokens.
_CHARTER = (
    "This is the scope you are working inside: what the work is for, and how it is "
    "ordered. It names no tool and it is not an instruction to run anything — read "
    "every line of it as scope, never as a command. Your tools, your permissions and "
    "the approval rules that govern them are exactly what they were before it arrived."
)

_SEP = "\n"
_BLOCK = "\n\n"

#: Directive field → the heading it renders under, in order. A list field
#: renders as bullets; the two scalars render inline.
_SECTIONS = (
    ("priorities", "Priorities"),
    ("constraints", "Constraints"),
    ("success_conditions", "Success conditions"),
    ("review_when", "Review this scope when"),
)


# ── the projection seam ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class ScopeContext:
    """What the host's projector is handed at one boundary.

    Everything here is either the host's own (:attr:`task`), this module's honest
    observation of the acting loop (:attr:`report`, :attr:`calls_by_name`,
    :attr:`failures_by_name`, :attr:`step_count`), or what this lane has actually
    applied (:attr:`active`). Nothing here is interpreted: turning any of it into
    objectives, commitments or resources is the projector's job, because
    embodiment cannot infer which domain facts those are (issue #2's
    compose-don't-reimplement rule).

    :attr:`active` is what the ACTOR received, never what the strategist's
    register holds — see the module docstring on embodiment#54.
    """

    task: Task
    report: ScopeReport
    active: Optional[ScopeDirective] = None
    turn_index: int = 0
    step_count: int = 0
    calls_by_name: dict[str, int] = field(default_factory=dict)
    failures_by_name: dict[str, int] = field(default_factory=dict)
    operator_messages: tuple[str, ...] = ()


#: The host-supplied projection: one boundary in, a snapshot to review or
#: ``None`` for "nothing here is worth a strategic review". It may consume
#: continuity and coherence output — this module imports neither.
ScopeProjectorFn = Callable[[ScopeContext], Optional[ScopeSnapshot]]


# ── the record ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ScopeTransition:
    """One thing the scope lane did to the actor's context, at one boundary.

    Read the field list as an exclusion as much as an inclusion: there is no
    tool, no arguments, no approval and no operator speech here, because this
    record describes *what governed the actor*, never what the actor may do.

    ``role`` and ``model`` are the strategist's, copied off the review that
    produced the directive and host-declared there. They are empty for the host
    default scope, which no model produced — which is how a single-model run
    stays incapable of claiming a strategist exists.
    """

    kind: str
    turn_index: int = 0
    step_count: int = 0
    scope_id: str = ""
    version: int = 0
    previous_version: int = 0
    """What :attr:`version` replaced — captured just before :meth:`_Governed._seat`
    overwrites the applied version, so an APPLIED/DEFAULT record names both the
    scope that governed before and the one that governs now. ``0`` (the applied
    chain's own starting value) on every other transition kind, where no seat
    happened and the field is not meaningful."""
    supersedes: Optional[str] = None
    snapshot_id: str = ""
    reason: str = ""
    role: str = ""
    model: str = ""
    text: str = ""
    """The exact bytes inserted into the actor's context, or ``""`` when nothing
    was inserted. On the record so "what did the actor actually read?" is
    answerable from the artifact rather than reconstructed."""
    lane: str = ""
    """Which persistence lane this drive ran in — :data:`~embodiment.scope.
    LANE_DURABLE` or :data:`~embodiment.scope.LANE_SESSION` (task t13,
    criterion 3: every scope record names its lane). Empty only on a drive with
    no scope lane at all, where it would be a claim rather than a fact."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "turn_index": self.turn_index,
            "step_count": self.step_count,
            "scope_id": self.scope_id,
            "version": self.version,
            "previous_version": self.previous_version,
            "supersedes": self.supersedes,
            "snapshot_id": self.snapshot_id,
            "reason": self.reason,
            "role": self.role,
            "model": self.model,
            "text": self.text,
            "lane": self.lane,
        }


# ── the persistence lanes ─────────────────────────────────────────────────────


def _text(value: Any) -> str:
    """Best-effort text. Never raises; an unrenderable value reads as empty."""
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:  # noqa: BLE001  # an unstringable value renders as empty
        return ""


def _wired(port: Any) -> bool:
    """Whether a persistence port carries a callable at all. Never raises."""
    if port is None:
        return False
    try:
        return port.load is not None or port.save is not None
    except Exception:  # noqa: BLE001  # a hostile port is simply not a port
        return False


@dataclass(frozen=True)
class ScopePersistence:
    """The durable lane's **host-visible seam**: two callables, and no backend.

    Which component actually stores the payload is not embodiment's to choose —
    the frame parks it as open vagueness ``v5`` (host state round-tripped
    through the scope projector, versus a continuity record). So this is a port
    and nothing else: a host supplies the two ends, and this package supplies
    the payload shape (:meth:`embodiment.scope.ScopeRegister.to_dict`) and the
    discipline around calling them.

    Frozen for :class:`~embodiment.scope.ScopeToolBench`'s reason: a host cannot
    bolt a third capability onto a port after construction, so the surface stays
    the two fields below forever.

    Fields
    ------
    load:
        ``() -> payload`` — called **once**, when the lane opens. Anything it
        returns that :meth:`embodiment.scope.ScopeRegister.from_dict` cannot
        read restores an empty lane; anything it *raises* is recorded and
        **disables writing for the drive**, because overwriting a store that
        could not be read would destroy the durable lane rather than degrade it.
    save:
        ``(payload) -> None`` — called after a directive is applied, and only
        when the chain actually changed. A raise is recorded once and disables
        further writes: durability is lost, delivery is not, and the record says
        exactly that.

    Both are optional. A port with neither is not a lane at all and the drive
    runs session-scoped.
    """

    load: Optional[Callable[[], Any]] = None
    save: Optional[Callable[[dict[str, Any]], None]] = None

    @property
    def wired(self) -> bool:
        """Whether this port can do anything. ``False`` ⇒ no durable lane."""
        return _wired(self)


class ScopeSession:
    """One session's scope state: in-process persistence, and nothing durable.

    A session is the second half of the layered persistence claim ``c33``. It
    holds its own :class:`~embodiment.scope.ScopeRegister` in the
    :data:`~embodiment.scope.LANE_SESSION` lane, so directives applied under it
    survive **across drives inside one process** and reach no store at all — a
    governed drive that has a session holds no :class:`ScopePersistence` port,
    so "a session never writes the durable lane" is structural rather than a
    branch that could be got wrong.

    :meth:`close` ends the session: the chain is dropped, :attr:`active` is
    ``None``, and a later drive handed this session starts under no scope with a
    recorded withholding naming it. A drive **already in flight** keeps the
    reference it opened with for the rest of that drive — an actor cannot un-read
    a message it was already shown, and pretending otherwise would put a claim in
    the record that was never true.

    Sessions are the **future seam for per-subagent scoping**: one session per
    subagent is the shape that fits, and two sessions already hold independent
    scope. Nothing here builds that scoping, and no subagent type is named.
    """

    def __init__(self, session_id: str = "", *, default: Optional[ScopeDirective] = None) -> None:
        self._id = _text(session_id)
        self._register: Optional[ScopeRegister] = ScopeRegister(default=default, lane=LANE_SESSION)

    @property
    def session_id(self) -> str:
        """The host's identifier for this session. Rides every record it earns."""
        return self._id

    @property
    def lane(self) -> str:
        """Always :data:`~embodiment.scope.LANE_SESSION`. A session has no other."""
        return LANE_SESSION

    @property
    def open(self) -> bool:
        """Whether this session still holds scope state."""
        return self._register is not None

    @property
    def register(self) -> Optional[ScopeRegister]:
        """This session's chain, or ``None`` once it has been closed."""
        return self._register

    @property
    def active(self) -> Optional[ScopeDirective]:
        """The directive this session is holding, or ``None``."""
        return self._register.active if self._register is not None else None

    def to_dict(self) -> dict[str, Any]:
        """This session's state, in the same payload shape the durable lane uses.

        For a host that wants to *inspect* a session — a debug surface, a live
        view. Handing it to a store would make it durable, which is precisely
        what a session is not; nothing in this module ever does.
        """
        payload = (
            self._register.to_dict()
            if self._register is not None
            else {
                "schema_version": LANE_SCHEMA_VERSION,
                "lane": LANE_SESSION,
                "accepted": [],
            }
        )
        payload["session_id"] = self._id
        return payload

    def close(self) -> None:
        """End the session. Idempotent, and never raises."""
        self._register = None


# ── controls ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ScopedControls:
    """This layer's own knobs. Every default keeps the strategist quiet.

    Fields
    ------
    review_every:
        Offer a projection every N turn boundaries even when nothing material
        changed. ``0`` — the default — means material change (plus the opening
        boundary) is the only trigger, which is the honest reading of "ordinary
        tool steps do not become strategic reports".
    blocked_after:
        How many failed calls to ONE tool make the report say
        :data:`~embodiment.scope.SCOPE_STATUS_BLOCKED`. Repeated failure is the
        one material signal a tool loop can observe without domain knowledge.
    max_report_entries:
        Cap on each report list. A report is a prompt input one layer up.
    max_entry_chars:
        Cap on one report entry's text, for the same reason.
    """

    review_every: int = 0
    blocked_after: int = 3
    max_report_entries: int = 12
    max_entry_chars: int = 300


@dataclass(frozen=True)
class ScopeGovernor:
    """Everything the scope lane needs, in one host-constructed object.

    Grouped rather than spread across :func:`run_scoped`'s signature so the
    composition stays reviewable and lands well under the parameter limit the
    ``strategist_runner`` cycle already refused to inherit debt on.

    Args:
        strategist: the background lane — a
            :class:`~embodiment.strategist_runner.StrategistRunner`, or anything
            with its ``start`` / ``consider`` / ``drain`` / ``degradation``
            surface. ``None`` means no strategist; with no default scope either,
            the whole lane is inert and the drive is byte-identical to ``run``.
        projector: the host's :data:`ScopeProjectorFn`. Without one no snapshot
            is ever built — this module never infers what the world means.
        default_scope: the **explicit host-derived** scope the actor operates
            under until a directive arrives. It is validated by
            :class:`~embodiment.scope.ScopeRegister` before it is seated, so a
            default that governs nothing is refused and recorded rather than
            silently seating scope nobody could name. Absent, the actor simply
            starts under no scope: nothing here fabricates one.
        identity: the resolved teammate identity, or ``None``. Handed to
            :func:`~embodiment.framing.frame_cortex` and used nowhere else. A
            host holding a :class:`~embodiment.framing.Framing` passes its
            ``identity``; there is no second resolution order here.
        controls: :class:`ScopedControls`; the defaults apply when omitted.
        persistence: the **durable** lane's host-visible port
            (:class:`ScopePersistence`). Absent — the default — no chain
            outlives the drive.
        session: the **session-scoped** lane (:class:`ScopeSession`). A session
            is the narrower lane and **wins**: a drive that has one never opens
            the durable port, even when a host wired both. That is the whole of
            "a session holds its own scope state without touching the durable
            lane", and it is a structural fact rather than a rule this module
            has to remember to obey (:class:`_Governed` drops the port).
    """

    strategist: Optional[Any] = None
    projector: Optional[ScopeProjectorFn] = None
    default_scope: Optional[ScopeDirective] = None
    identity: Optional[str] = None
    controls: Optional[ScopedControls] = None
    persistence: Optional[ScopePersistence] = None
    session: Optional[ScopeSession] = None

    @property
    def lane(self) -> str:
        """Which persistence lane a drive under this governor runs in."""
        if self.session is not None:
            return LANE_SESSION
        return LANE_DURABLE if _wired(self.persistence) else LANE_SESSION

    @property
    def armed(self) -> bool:
        """Whether the lane does anything at all. ``False`` ⇒ pure pass-through."""
        return (
            self.strategist is not None
            or self.default_scope is not None
            or self.session is not None
            or _wired(self.persistence)
        )


# ── the outcome ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ScopedOutcome:
    """A governed drive: the actor's own outcome, plus what scope did to it.

    :attr:`outcome` is the object :func:`embodiment.loop.run` returned — the same
    object, not a copy — so a host that only wants the drive reads it and gets
    exactly what an ungoverned drive would have produced.
    """

    outcome: LoopOutcome
    transitions: tuple[ScopeTransition, ...] = ()
    counts: dict[str, int] = field(default_factory=dict)
    active: Optional[ScopeDirective] = None
    """The directive the actor was last governed by — what was RECEIVED, never
    what the strategist's register holds (embodiment#54)."""
    scope_degradations: tuple[Any, ...] = ()
    """The strategist lane's own ledger, relayed rather than re-minted."""
    lane: str = ""
    """Which persistence lane governed this drive — :data:`~embodiment.scope.
    LANE_DURABLE`, :data:`~embodiment.scope.LANE_SESSION`, or ``""`` for a drive
    with no scope lane at all (task t13)."""

    @property
    def result(self) -> TaskResult:
        return self.outcome.result

    @property
    def exit_reason(self) -> str:
        return self.outcome.exit_reason

    @property
    def applications(self) -> tuple[ScopeTransition, ...]:
        """Only the transitions that actually put scope in front of the actor."""
        return tuple(
            entry
            for entry in self.transitions
            if entry.kind in (TRANSITION_DEFAULT, TRANSITION_APPLIED)
        )

    def to_dict(self) -> dict[str, Any]:
        """A JSON-ready record. The loop outcome is NOT folded in: it has its own."""
        return {
            "exit_reason": self.exit_reason,
            "transitions": [entry.to_dict() for entry in self.transitions],
            "counts": dict(self.counts),
            "active": self.active.to_dict() if self.active is not None else None,
            "scope_degradations": [_as_dict(entry) for entry in self.scope_degradations],
            "lane": self.lane,
        }


# ── rendering: the ONE prompt-bearing path ────────────────────────────────────


def render_directive(directive: Any) -> str:
    """Render one directive as the base text a scope event carries.

    Deterministic, ordered and self-describing: a reader of the artifact can
    match the inserted bytes to the directive that produced them. It reads a
    :class:`~embodiment.scope.ScopeDirective`'s declared fields and nothing else,
    so there is no field a payload could smuggle text through — the directive
    shape already refused those, and this cannot re-open them.

    It does **not** guard its own attribute reads. A hostile object is caught one
    level up, at the single place a drained review is consumed, so the guard is
    where the record can be written rather than scattered through the prose.
    """
    blocks = [
        _HEADER.format(scope_id=directive.scope_id, version=directive.version),
        f"Objective: {directive.objective}",
    ]
    for name, heading in _SECTIONS:
        entries = tuple(getattr(directive, name))
        if entries:
            blocks.append(_bullets(heading, entries))
    responsibilities = tuple(directive.responsibilities)
    if responsibilities:
        blocks.append(
            _bullets(
                "Responsibilities",
                tuple(f"{entry.owner}: {entry.responsibility}" for entry in responsibilities),
            )
        )
    if directive.decision_summary:
        blocks.append(f"Why this scope: {directive.decision_summary}")
    blocks.append(_CHARTER)
    return _BLOCK.join(blocks)


def _bullets(heading: str, entries: tuple[str, ...]) -> str:
    return heading + ":" + _SEP + _SEP.join(f"- {entry}" for entry in entries)


def _scope_event(directive: Any, *, identity: Optional[str]) -> dict[str, Any]:
    """The ONE place a message reaching the actor is built.

    Its content is :func:`~embodiment.framing.frame_cortex`'s output and nothing
    else, which is what makes "directives render exclusively through framing
    composition" checkable rather than asserted (``h22``). ``muse=False`` is
    fixed: a scope event names scope, not the rig's advisory shape.
    """
    return {
        "role": _EVENT_ROLE,
        "content": frame_cortex(render_directive(directive), identity=identity, muse=False),
    }


# ── the public entry point ────────────────────────────────────────────────────


def run_scoped(
    complete: CompleteFn,
    task: Task,
    *,
    executor: ToolExecutor,
    max_steps: int,
    governor: Optional[ScopeGovernor] = None,
    **actor_kwargs: Any,
) -> ScopedOutcome:
    """Drive :func:`embodiment.loop.run` under an optional strategic scope.

    Args:
        complete: the actor's model seam, exactly as ``run`` takes it. It is
            wrapped only when the lane is armed; otherwise this object is what
            ``run`` receives.
        task: the work item.
        executor: the tool surface, injected and forwarded untouched. What the
            model may do stays entirely the host's — this layer holds no
            approval path and constructs no executor.
        max_steps: the model-turn budget. The scope lane spends none of it: a
            review runs on its own thread and a directive costs one appended
            message, never a turn.
        governor: the scope lane (:class:`ScopeGovernor`). ``None``, or one with
            neither a strategist nor a default scope, leaves the drive
            byte-identical to ``run`` — the host's own ``complete``,
            ``progress`` and ``operator_inbox`` objects are passed through by
            identity and nothing is recorded.
        **actor_kwargs: every other keyword ``run`` declares
            (``system_prompt``, ``hooks``, ``observer``, ``presence``,
            ``continuity``, ``subagent``, ``controls``, ``model``, …),
            forwarded verbatim. An unknown keyword is refused by ``run``'s own
            signature, which is what makes "no new ``run`` parameter" a
            structural fact rather than a promise. ``progress`` and
            ``operator_inbox`` are the two this layer wraps; both still reach
            ``run``, and the host's own callable is still what is called.

    Returns:
        A :class:`ScopedOutcome` carrying ``run``'s own outcome object plus the
        scope record: what was applied, what was withheld, what degraded.

    Raises:
        LoopAborted: whatever ``run`` raises, unchanged — the host's failure
            surfacing, never this layer's. The scope record is attached to the
            exception as ``scoped_outcome`` so a degradation is not lost on the
            path where it is most interesting.
    """
    # Read, never popped: the actor's OWN LoopEvent stream still reaches this
    # same observer through ``run`` below, unaffected by scope also using it.
    host_observer = actor_kwargs.get("observer")
    lane = _Governed(
        governor if governor is not None else ScopeGovernor(), task, observer=host_observer
    )
    lane.arm()
    host_progress = actor_kwargs.pop("progress", None)
    host_inbox = actor_kwargs.pop("operator_inbox", None)
    try:
        outcome = run(
            lane.complete(complete),
            task,
            executor=executor,
            max_steps=max_steps,
            progress=lane.progress(host_progress),
            operator_inbox=lane.inbox(host_inbox),
            **actor_kwargs,
        )
    except LoopAborted as exc:
        exc.scoped_outcome = lane.finish(exc.outcome)  # type: ignore[attr-defined]
        raise
    return lane.finish(outcome)


# ── the lane ──────────────────────────────────────────────────────────────────


class _Governed:
    """One drive's scope state. Constructed per :func:`run_scoped` call, never reused.

    Every method that touches host or strategist code is guarded and records a
    :class:`ScopeTransition` on the way out. The guarantee is compositional, as
    it is in :mod:`embodiment.scope`: there is no outer ``try`` anywhere here,
    because each place that can fault carries its own.
    """

    def __init__(
        self,
        governor: ScopeGovernor,
        task: Task,
        *,
        observer: Optional[Callable[[Any], None]] = None,
    ) -> None:
        self._gov = governor
        self._on = governor.armed
        self._controls = governor.controls or ScopedControls()
        self._task = task
        # ── the persistence lane (task t13). A session is the narrower lane and
        # wins: with one, the durable port is not merely unused, it is not held.
        self._session = governor.session
        self._port: Optional[ScopePersistence] = (
            None
            if self._session is not None or not _wired(governor.persistence)
            else governor.persistence
        )
        self._lane = governor.lane if self._on else ""
        #: The APPLIED chain as lane state — fed by :meth:`_seat`, never by a
        #: strategist's register (embodiment#54), and what :meth:`_persist`
        #: writes. Replaced by :meth:`_open_lane` with the lane's real chain.
        self._register = ScopeRegister(lane=self._lane or LANE_SESSION)
        #: Whether the durable lane may still be written. Cleared by an
        #: unreadable store or a failed write, each recorded once.
        self._writable = self._port is not None
        #: What this lane believes the store already holds, so an unchanged
        #: chain is never rewritten.
        self._written: Optional[dict[str, Any]] = None
        self._active: Optional[ScopeDirective] = None
        self._version = _NO_VERSION
        self._applied: list[str] = []
        self._queued: Optional[ScopeDirective] = None
        self._queued_kind = TRANSITION_DEFAULT
        self._transitions: list[ScopeTransition] = []
        self._degraded = False
        self._turn = 0
        self._steps = 0
        self._calls: dict[str, int] = {}
        self._failures: dict[str, int] = {}
        self._operator: list[str] = []
        self._snapshot: Optional[ScopeSnapshot] = None
        self._report: Optional[ScopeReport] = None
        # ── observability (task t5): the SAME observer the host wired for the
        # actor's own LoopEvent stream, translated by embodiment.scope_events.
        self._observer = observer
        self._observer_failed = False
        self._strategist_role, self._strategist_model = scope_events.strategist_identity(
            governor.strategist
        )
        self._counts = {
            # intake — what happened at each turn boundary
            "boundaries": 0,
            "boundaries_projected": 0,
            "boundaries_immaterial": 0,
            "boundaries_ungoverned": 0,
            "snapshots_offered": 0,
            "snapshots_unchanged": 0,
            "snapshots_absent": 0,
            "projector_failures": 0,
            "offers_failed": 0,
            # outcomes — what happened to reviews that reached this layer
            "outcomes_drained": 0,
            "directives_applied": 0,
            "holds_recorded": 0,
            "outcomes_withheld": 0,
            # persistence — what the lane restored, and what it wrote
            "scope_restored": 0,
            "durable_writes": 0,
        }

    # ── lifecycle ────────────────────────────────────────────────────────────
    def arm(self) -> None:
        """Open the persistence lane, seat the host default, start the strategist.

        The order is load-bearing: whatever the lane already holds outranks the
        host default, because a default is what governs *until* a directive
        arrives and one already has.
        """
        if not self._on:
            return
        self._open_lane()
        self._seat_default()
        self._start()

    def finish(self, outcome: LoopOutcome) -> ScopedOutcome:
        """Fold the drive into a :class:`ScopedOutcome`. No terminal drain, by design."""
        relayed = self._relayed()
        for entry in relayed:
            self._notify(
                scope_events.for_lane_degradation(
                    entry,
                    model=self._strategist_model,
                    role=self._strategist_role,
                    lane=self._lane,
                )
            )
        return ScopedOutcome(
            outcome=outcome,
            transitions=tuple(self._transitions),
            counts=dict(self._counts),
            active=self._active,
            scope_degradations=relayed,
            lane=self._lane,
        )

    def _notify(self, event: Optional[Any]) -> None:
        """Offer one translated scope event to the host's observer. Never raises (C3).

        Mirrors :mod:`embodiment.loop`'s own ``_observe``: a raising observer is
        recorded once (by simply disabling further notification — the observer
        itself already has its own degradation surface if it wants one, e.g.
        :class:`~embodiment.events.EventEmitter`) rather than retried at every
        subsequent occurrence.
        """
        if event is None or self._observer is None or self._observer_failed:
            return
        try:
            self._observer(event)
        except Exception:  # noqa: BLE001  # an observer must never abort a drive
            self._observer_failed = True

    # ── the persistence lane (task t13) ──────────────────────────────────────
    def _open_lane(self) -> None:
        """Open this drive's lane and queue whatever scope it already holds.

        Restored scope is delivered like any other directive — at the first turn
        boundary, through the one framing-composed path — and recorded as
        :data:`TRANSITION_APPLIED` rather than :data:`TRANSITION_DEFAULT`,
        because a restored directive is model-produced scope and a default is
        not. Never raises: every host call below carries its own guard.
        """
        self._register = self._lane_register()
        restored = self._register.active
        if restored is None:
            return
        self._counts["scope_restored"] += 1
        self._seat(restored)
        self._queued, self._queued_kind = restored, TRANSITION_APPLIED

    def _lane_register(self) -> ScopeRegister:
        """The chain this drive governs from: the session's, the store's, or new."""
        if self._session is not None:
            return self._session_register()
        if self._port is not None:
            return self._durable_register()
        return ScopeRegister(lane=LANE_SESSION)

    def _session_register(self) -> ScopeRegister:
        """The session's own chain — or a fresh one, recorded, when it has closed."""
        try:
            held, label = self._session.register, _text(self._session.session_id)
        except Exception as exc:  # noqa: BLE001  # a hostile session is never a crash
            held, label = None, f"<unreadable: {type(exc).__name__}: {exc}>"
        if isinstance(held, ScopeRegister):
            return held
        self._record(
            TRANSITION_WITHHELD,
            reason=(
                f"session {label!r} is closed; the scope it held did not outlive it, "
                "so this drive starts from the host default scope"
            ),
        )
        return ScopeRegister(lane=LANE_SESSION)

    def _durable_register(self) -> ScopeRegister:
        """Read the durable lane through the host's port. Never raises (C3).

        A store that could not be READ disables writing for the whole drive:
        overwriting it would replace scope nobody could see with scope from a
        single drive, which destroys the durable lane rather than degrading it.

        The port's own attribute read sits inside the guard with the call: a
        host may hand any object with the two names, so reaching for ``load``
        is as much host code as calling it.
        """
        try:
            loader = self._port.load
            payload = loader() if loader is not None else None
        except Exception as exc:  # noqa: BLE001  # a dead store is never a crash
            self._writable = False
            self._record(
                TRANSITION_DEGRADED,
                reason=(
                    f"the durable scope lane could not be read ({type(exc).__name__}: {exc}); "
                    "nothing will be written to it on this drive"
                ),
            )
            return ScopeRegister(lane=LANE_DURABLE)
        register = ScopeRegister.from_dict(payload, lane=LANE_DURABLE)
        self._written = register.to_dict()
        for refusal in register.rejections:
            self._record(
                TRANSITION_WITHHELD,
                scope_id=refusal.scope_id,
                version=refusal.version,
                reason=f"a persisted directive was refused on restore: {refusal.reason}",
            )
        return register

    def _persist(self) -> None:
        """Write the applied chain through the host's port. Never raises (C3).

        Skipped when the chain has not changed, so a drive that applies nothing
        writes nothing. A failed write costs **durability only** — the directive
        already governs this drive — and the record says exactly that rather
        than letting a host read it as a delivery failure.
        """
        if not self._writable:
            return
        payload = self._register.to_dict()
        if payload == self._written:
            return
        try:
            writer = self._port.save
            if writer is None:
                return
            writer(payload)
        except Exception as exc:  # noqa: BLE001  # a failed write never aborts a drive
            self._writable = False
            self._record(
                TRANSITION_DEGRADED,
                reason=(
                    f"the durable scope lane could not be written "
                    f"({type(exc).__name__}: {exc}); {self._under()}, but that scope "
                    "will not survive this drive"
                ),
            )
            return
        self._written = payload
        self._counts["durable_writes"] += 1

    def _seat_default(self) -> None:
        """Validate and seat the explicit host default, or record why not.

        Validation is :class:`~embodiment.scope.ScopeRegister`'s, reused rather
        than restated: a fresh register admits the default on exactly the terms
        every later directive is admitted on, and a refusal arrives already
        shaped as a :class:`~embodiment.scope.ScopeRejection` with its reason.

        A default governs *until a directive arrives*, so a lane that already
        carries one keeps it: the default is not offered at all rather than
        offered and refused as a duplicate, which would put a refusal in the
        record for a host that did nothing wrong.
        """
        directive = self._gov.default_scope
        if directive is None or self._register.active is not None:
            return
        register = ScopeRegister(default=directive)
        seated = register.active
        if seated is None:
            refused = register.rejections[-1] if register.rejections else None
            self._record(
                TRANSITION_WITHHELD,
                scope_id=refused.scope_id if refused is not None else "",
                reason=(
                    "the host default scope was refused: "
                    + (refused.reason if refused is not None else "it governs nothing")
                ),
            )
            return
        self._seat(seated)
        self._queued, self._queued_kind = seated, TRANSITION_DEFAULT

    def _start(self) -> None:
        """Start the strategist lane, or record that the drive runs without one."""
        strategist = self._gov.strategist
        if strategist is None:
            return
        try:
            started = bool(strategist.start())
            why = "the lane refused to start"
        except Exception as exc:  # noqa: BLE001  # a dead lane is never a crash
            started, why = False, f"{type(exc).__name__}: {exc}"
        if started:
            return
        self._degraded = True
        self._record(
            TRANSITION_DEGRADED,
            role=self._strategist_role,
            model=self._strategist_model,
            reason=f"the strategist lane never started ({why}); {self._under()}",
        )

    # ── the wrapped seams ────────────────────────────────────────────────────
    def complete(self, inner: CompleteFn) -> CompleteFn:
        """The turn boundary — and the ONLY place scope reaches the actor."""
        if not self._on:
            return inner

        def governed(messages: list[dict[str, Any]]) -> Any:
            self._boundary(messages)
            return inner(messages)

        return governed

    def progress(self, inner: Any) -> Any:
        """Watch what the actor actually did. Observe-only, and never raises."""
        if not self._on:
            return inner

        def governed(step_index: int, tool: str, arguments: Any, ok: bool) -> None:
            self._note_step(step_index, tool, ok)
            if inner is not None:
                inner(step_index, tool, arguments, ok)

        return governed

    def inbox(self, inner: Any) -> Any:
        """Tee operator intent. The host's callable is still what ``run`` polls."""
        if not self._on or inner is None:
            return inner

        def governed() -> Any:
            pending = inner()
            self._note_operator(pending)
            return pending

        return governed

    # ── the boundary ─────────────────────────────────────────────────────────
    def _boundary(self, messages: list[dict[str, Any]]) -> None:
        """One turn boundary: deliver, check the lane, offer the next projection."""
        self._turn += 1
        self._counts["boundaries"] += 1
        self._deliver(messages)
        self._note_lane()
        self._offer()
        self._operator.clear()

    def _deliver(self, messages: list[dict[str, Any]]) -> None:
        """Put whatever is due in front of the actor. The queued scope goes first.

        What is queued is either the host default or what the persistence lane
        already held; :attr:`_queued_kind` is which, decided where the queuing
        happened rather than guessed here.
        """
        queued, self._queued = self._queued, None
        if queued is not None:
            self._emit(messages, self._queued_kind, queued)
        if self._degraded:
            return
        for outcome in self._drain():
            try:
                self._consume(messages, outcome)
            except Exception as exc:  # noqa: BLE001  # an unreadable review is recorded
                self._withhold(f"the review could not be read: {type(exc).__name__}: {exc}")

    def _drain(self) -> list[Any]:
        """What REACHED the actor — never the register (embodiment#54). Never raises."""
        strategist = self._gov.strategist
        if strategist is None:
            return []
        try:
            outcomes = list(strategist.drain(step_count=self._steps))
        except Exception as exc:  # noqa: BLE001  # a hostile lane is never a crash
            self._withhold(f"the strategist drain failed: {type(exc).__name__}: {exc}")
            return []
        self._counts["outcomes_drained"] += len(outcomes)
        for outcome in outcomes:
            # What the strategist produced, independent of what this layer goes
            # on to decide about it (applied / held / withheld, below).
            self._notify(scope_events.review_completed_event(outcome, lane=self._lane))
            self._notify(scope_events.directive_proposed_event(outcome, lane=self._lane))
        return outcomes

    def _consume(self, messages: list[dict[str, Any]], outcome: Any) -> None:
        """Decide what ONE drained review does to the actor's scope.

        Reads the outcome plainly; :meth:`_deliver` holds the guard, so a hostile
        object lands as one recorded withholding rather than a scattered set of
        defensive defaults that would each have to be honest on their own.
        """
        directive = outcome.directive
        snapshot_id = outcome.snapshot_id
        role, model = outcome.role, outcome.model
        if directive is None:
            if outcome.exit_reason == SCOPE_EXIT_UNCHANGED:
                self._counts["holds_recorded"] += 1
                self._record(
                    TRANSITION_HELD,
                    snapshot_id=snapshot_id,
                    role=role,
                    model=model,
                    reason=f"the strategist held the current scope; {self._under()}",
                )
                return
            self._withhold(
                f"the review exited {outcome.exit_reason!r} with no directive; {self._under()}",
                snapshot_id=snapshot_id,
                role=role,
                model=model,
            )
            return
        scope_id, version = directive.scope_id, directive.version
        if scope_id in self._applied:
            self._withhold(
                f"scope {scope_id!r} was already applied to this drive",
                scope_id=scope_id,
                version=version,
                snapshot_id=snapshot_id,
                role=role,
                model=model,
            )
            return
        if version <= self._version:
            self._withhold(
                f"version {version} does not advance on the applied version "
                f"{self._version}; applying it could restore superseded scope",
                scope_id=scope_id,
                version=version,
                snapshot_id=snapshot_id,
                role=role,
                model=model,
            )
            return
        self._emit(messages, TRANSITION_APPLIED, directive, snapshot_id, role, model)

    def _emit(
        self,
        messages: list[dict[str, Any]],
        kind: str,
        directive: Any,
        snapshot_id: str = "",
        role: str = "",
        model: str = "",
    ) -> None:
        """Insert one framing-composed event into the turn stream, and record it."""
        event = _scope_event(directive, identity=self._gov.identity)
        messages.append(event)
        previous_version = self._version
        self._seat(directive)
        if kind == TRANSITION_APPLIED:
            self._counts["directives_applied"] += 1
        self._record(
            kind,
            scope_id=directive.scope_id,
            version=directive.version,
            previous_version=previous_version,
            supersedes=directive.supersedes,
            snapshot_id=snapshot_id,
            role=role,
            model=model,
            text=event["content"],
            reason=f"scope {directive.scope_id!r} now governs this drive",
        )
        self._persist()

    def _seat(self, directive: ScopeDirective) -> None:
        """Make *directive* what the actor is working under. The applied chain."""
        self._active = directive
        self._version = directive.version
        if directive.scope_id not in self._applied:
            self._applied.append(directive.scope_id)
        self._admit(directive)

    def _admit(self, directive: ScopeDirective) -> None:
        """Record the directive on the LANE's chain — the state that persists.

        :meth:`~embodiment.scope.ScopeRegister.receive` rather than ``offer``:
        this chain is what the actor *received*, so its provenance legitimately
        has gaps and re-checking ``supersedes`` against it would strand the
        actor (the module docstring's own reasoning, applied to the state that
        outlives the drive). A directive the lane already holds — the restored
        one, or the default seated twice — is not recorded twice.
        """
        if directive.scope_id in self._register.known:
            return
        refusal = self._register.receive(directive)
        if refusal is None:
            return
        self._record(
            TRANSITION_DEGRADED,
            scope_id=refusal.scope_id,
            version=refusal.version,
            reason=(
                f"the {self._lane} scope lane refused to record scope "
                f"{refusal.scope_id!r} ({refusal.reason}); it governs this drive "
                "but is absent from the lane's chain"
            ),
        )

    def _note_lane(self) -> None:
        """Notice the lane having stopped, once, and say what survives it."""
        strategist = self._gov.strategist
        if strategist is None or self._degraded:
            return
        try:
            reason = strategist.degradation()
        except Exception as exc:  # noqa: BLE001  # even asking must not crash a drive
            reason = f"{type(exc).__name__}: {exc}"
        if not reason:
            return
        self._degraded = True
        self._record(
            TRANSITION_DEGRADED,
            role=self._strategist_role,
            model=self._strategist_model,
            reason=f"the strategist lane stopped ({reason}); {self._under()}",
        )

    # ── projection ───────────────────────────────────────────────────────────
    def _offer(self) -> None:
        """Build a report, ask the host what it means, and offer the result upward."""
        strategist = self._gov.strategist
        projector = self._gov.projector
        if strategist is None or projector is None or self._degraded:
            self._counts["boundaries_ungoverned"] += 1
            return
        report = self._build_report()
        if not self._due(report):
            self._counts["boundaries_immaterial"] += 1
            return
        self._counts["boundaries_projected"] += 1
        self._report = report
        self._notify(
            scope_events.report_event(
                report, turn_index=self._turn, step_count=self._steps, lane=self._lane
            )
        )
        try:
            snapshot = projector(self._context(report))
        except Exception as exc:  # noqa: BLE001  # a host projector never aborts a drive
            self._counts["projector_failures"] += 1
            self._record(
                TRANSITION_UNPROJECTED,
                reason=f"the host scope projector failed: {type(exc).__name__}: {exc}",
            )
            return
        if snapshot is None:
            self._counts["snapshots_absent"] += 1
            return
        if self._same(snapshot):
            self._counts["snapshots_unchanged"] += 1
            return
        self._snapshot = snapshot
        self._notify(
            scope_events.snapshot_event(
                snapshot, turn_index=self._turn, step_count=self._steps, lane=self._lane
            )
        )
        try:
            strategist.consider(snapshot, step_index=self._steps)
        except Exception as exc:  # noqa: BLE001  # a hostile lane is never a crash
            self._counts["offers_failed"] += 1
            self._record(
                TRANSITION_UNPROJECTED,
                reason=f"the snapshot could not be offered: {type(exc).__name__}: {exc}",
            )
            return
        self._counts["snapshots_offered"] += 1
        self._notify(
            scope_events.review_started_event(
                snapshot,
                step_index=self._steps,
                model=self._strategist_model,
                role=self._strategist_role,
                lane=self._lane,
            )
        )

    def _due(self, report: ScopeReport) -> bool:
        """Whether this boundary is worth a projection. Ordinary steps are not."""
        if self._report is None:
            return True
        if report != self._report:
            return True
        every = self._controls.review_every
        return every > 0 and self._turn % every == 0

    def _build_report(self) -> ScopeReport:
        """The typed upward record — only what a tool loop can honestly observe.

        ``material_outcomes``, ``conflicts``, ``new_constraints`` and
        ``commitments_at_risk`` stay empty here on purpose: this module watches
        steps, not a domain, and the projector is where host state becomes
        meaning.
        """
        threshold = max(1, self._controls.blocked_after)
        failures = tuple(
            self._clip(f"{tool}: {count} failed calls")
            for tool, count in sorted(self._failures.items())
            if count >= threshold
        )[: max(1, self._controls.max_report_entries)]
        requested = self._clip(self._operator[-1]) if self._operator else None
        return ScopeReport(
            scope_id=self._active.scope_id if self._active is not None else "",
            status=SCOPE_STATUS_BLOCKED if failures else SCOPE_STATUS_ACTIVE,
            repeated_failures=failures,
            requested_decision=requested,
        )

    def _context(self, report: ScopeReport) -> ScopeContext:
        return ScopeContext(
            task=self._task,
            report=report,
            active=self._active,
            turn_index=self._turn,
            step_count=self._steps,
            calls_by_name=dict(self._calls),
            failures_by_name=dict(self._failures),
            operator_messages=tuple(self._operator),
        )

    def _same(self, snapshot: Any) -> bool:
        """Whether this projection repeats the last one offered. Never raises."""
        try:
            return bool(snapshot == self._snapshot)
        except Exception:  # noqa: BLE001  # an uncomparable snapshot is simply new
            return False

    # ── observation ──────────────────────────────────────────────────────────
    def _note_step(self, step_index: Any, tool: Any, ok: Any) -> None:
        """Fold one acting step. Never raises: this runs on the actor's own path."""
        try:
            name = str(tool or "")
            if not name:
                return  # the loop's phase-notice sentinel, not a step
            self._steps = max(self._steps, int(step_index) + 1)
            self._calls[name] = self._calls.get(name, 0) + 1
            if not ok:
                self._failures[name] = self._failures.get(name, 0) + 1
        except Exception as exc:  # noqa: BLE001  # an unreadable step is recorded
            self._record(
                TRANSITION_UNPROJECTED,
                reason=f"an acting step could not be read: {type(exc).__name__}: {exc}",
            )

    def _note_operator(self, pending: Any) -> None:
        """Tee whatever the host's inbox returned. Never raises, never rewrites it."""
        try:
            for text in pending or ():
                self._operator.append(str(text))
        except Exception as exc:  # noqa: BLE001  # a junk inbox is recorded, not raised
            self._record(
                TRANSITION_UNPROJECTED,
                reason=f"operator intent could not be read: {type(exc).__name__}: {exc}",
            )

    # ── records ──────────────────────────────────────────────────────────────
    def _withhold(self, reason: str, **stamp: Any) -> None:
        self._counts["outcomes_withheld"] += 1
        self._record(TRANSITION_WITHHELD, reason=reason, **stamp)

    def _record(self, kind: str, **stamp: Any) -> None:
        # Every scope record names its lane (task t13, criterion 3), stamped in
        # the ONE place records are built rather than at each call site.
        stamp.setdefault("lane", self._lane)
        transition = ScopeTransition(
            kind=kind,
            turn_index=self._turn,
            step_count=self._steps,
            **stamp,
        )
        self._transitions.append(transition)
        self._notify(scope_events.for_transition(transition))

    def _under(self) -> str:
        """What the actor keeps working under — the phrase every degradation ends on."""
        if self._active is None:
            return "the actor continues under no scope"
        return (
            f"the actor continues under scope {self._active.scope_id!r} "
            f"(version {self._active.version})"
        )

    def _relayed(self) -> tuple[Any, ...]:
        """The strategist lane's OWN ledger, relayed. Never re-minted here."""
        strategist = self._gov.strategist
        if strategist is None:
            return ()
        try:
            return tuple(strategist.degradations)
        except Exception:  # noqa: BLE001  # an unreadable ledger reads as empty
            return ()

    def _clip(self, text: str) -> str:
        cap = max(1, self._controls.max_entry_chars)
        return text if len(text) <= cap else text[: cap - 1] + "…"


#: No directive applied yet. Matches :mod:`embodiment.scope`'s own sentinel, so a
#: version-zero default still admits a version-one directive after it.
_NO_VERSION = -1


def _as_dict(entry: Any) -> Any:
    """Serialize a relayed degradation without importing its type. Never raises."""
    reader = getattr(entry, "to_dict", None)
    try:
        return reader() if callable(reader) else _bare(entry)
    except Exception:  # noqa: BLE001  # an unreadable record is still reported
        return _bare(entry)


def _bare(entry: Any) -> dict[str, Any]:
    """The floor: a record that cannot describe itself still names itself."""
    return {"code": "", "reason": repr(entry)}
