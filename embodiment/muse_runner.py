"""The muse's THREAD — one daemon pump beside the actor loop (task t10b).

.. warning::

   **ARCHIVED — 2026-08-03, embodiment#53, deviations** ``d2`` **/** ``d3``.

   This module has left the shipped reference architecture, and it is the one
   the citation runs through: :mod:`embodiment.strategist_runner` is this file,
   copied verbatim and then owned outright. So archival here means *off the
   curated surface*, never deleted — roughly fourteen hundred lines of
   concurrency correctness that took live probes to settle (the single
   replaceable pending slot, the poll-wake read, the bounded join that never
   hangs on a parked blocking read) stay readable exactly so a reader can check
   the copy against its source.

   Reaching this lane now means naming ``embodiment.muse_runner`` explicitly;
   ``from embodiment import ThreadedMuseRunner`` no longer resolves. The
   archival supersedes confirmed claims ``c12`` and ``c32``. See
   ``tests/test_muse_archival.py`` for the disposition in executable form.

:mod:`embodiment.muse` (task t10a) is the muse's *reasoning*: a bounded,
tools-off thinking loop with no thread, no clock and no timer, deterministic to
the last branch. This module is the other half, and only the other half — the
**thread mechanics** that let that loop run beside the actor loop instead of
inside it. Nothing here reasons; nothing there concurs. That split is the whole
reason deviation ``d1``'s cost is bounded: the concurrency surface is one file,
one thread, one lock, and one queue-shaped buffer.

Deviation d1, and the cost it bought
------------------------------------
``d1`` replaced task t7's synchronous per-boundary advisory seam with a second,
parallel thinking loop that **embodiment owns the thread for**. That was chosen
knowingly: threading enters the library contract and deterministic tests get
harder. What follows is how that cost is kept small.

* **A museless run costs nothing.** No muse configured means no runner, and a
  runner that is never asked to consider anything starts no thread. Not a
  stopped thread, not an idle pool — none. The museless lane is the default
  tested path (``tests/test_muse_runner.py``), not a degraded exception.
* **The library's thread-free parts stay thread-free.**
  :mod:`embodiment.presence_engine` still imports no ``threading``, no ``time``
  and no ``asyncio``, pinned by an AST test; the thread lives HERE and reaches
  the pump only through the drain-shaped
  :class:`~embodiment.presence_engine.MuseSeam`.
* **The actor loop never waits.** :meth:`ThreadedMuseRunner.consider` hands over
  a boundary and returns; :meth:`ThreadedMuseRunner.drain` returns whatever
  finished, instantly. An empty drain is the normal case.

The thread discipline, inherited from ``colleague/realtime.py``
---------------------------------------------------------------
That module already solved a threaded pump correctly, and this one copies its
discipline (not its code):

* **ONE daemon thread.** No pool, no second pump, no thread per session.
* A :class:`threading.Event` **stop signal**, plus a wake event so teardown is
  prompt rather than parked on a poll interval.
* A **poll-wake wait**: the worker wakes on the event *or* on a bounded poll
  interval, and re-checks the stop flag either way, so a missed wakeup costs
  latency and never a hang.
* :func:`_bounded_join` — teardown **never hangs**, even when the muse is parked
  inside a model call nothing can interrupt. The thread is a daemon precisely so
  that an unreapable session cannot keep a host's process alive.
* **Degrade, never raise.** A start failure, a dead endpoint, a mid-run failure
  and a worker that dies each flip a degraded flag and record a transition. No
  exception ever crosses back into the actor loop's main path.

One close, for things this module knows nothing about
-----------------------------------------------------
A host that hands the muse a *tool* has a second lifetime to end — whatever the
tool holds open — and it has to end after the thinking thread stops, not
before. :data:`Closer` is that seam: ``ThreadedMuseRunner(complete,
closers=(workspace.close,))`` runs an opaque zero-argument callable once at
:meth:`ThreadedMuseRunner.close`, after the bounded join. The runner never
learns what it closed, imports nothing to support it, and a failure is recorded
(:data:`DEGRADED_CLOSER`) rather than raised. Ownership stays with whoever
constructed the thing; only the *timing* is delegated here.

Staleness and late arrival are RECORDED, never silent (C3)
-----------------------------------------------------------
Parallelism makes two new ways to lose a thought, and constraint C3 says a
presence layer may not lose one quietly:

* **Stale** — an insight reasoned about step 3 read at step 40. Judged with
  t10a's :func:`~embodiment.muse.is_stale` against the actor's current step
  (tracked monotonically, so a boundary carrying no step cannot make an ancient
  insight look current), and every drop is recorded.
* **Late** — an insight produced after the runner closed has nowhere to go, as
  does one still buffered when the actor finished. Both are recorded.
* **Overflow** — the buffer is bounded, so a muse outrunning the actor discards
  its OLDEST thinking (the freshest survives) and records each discard.
* **Superseded** — a boundary offered while a session is in flight replaces any
  boundary still queued, so the muse always thinks about the most recent
  position. The displaced boundary is recorded.
* **Starved** — a background compilation item that never reached the thread,
  because boundary counsel outranked it, a newer compilation request replaced
  it, or the lane closed first. Recorded.
* **Displaced** — boundary counsel discarded from a full buffer to make room for
  compilation output: the lower class evicting the higher. Recorded, and
  deliberately a *different* code from plain overflow.

…and so is DELIVERY, in its own stream (task t5)
------------------------------------------------
C3 makes loss observable. It does not make *arrival* observable, and the one
beat whose entire job is arrival — the terminal drain — needs to be: the cycle
that added it claims counsel formerly stranded at close now reaches the actor,
and a delivery path with no record would put that claim beyond checking.
:meth:`ThreadedMuseRunner.drain_terminal` therefore mints a
:class:`MuseDelivery` carrying the count and the delivered insight ids, **zero
included**, into a ledger of its own (:attr:`ThreadedMuseRunner.deliveries`).

It is deliberately not a ``DROPPED_*`` code. The degradation vocabulary answers
*"what went wrong?"*; a terminal drain that delivered three insights is this
lane working, and minting a degradation for it would make every healthy run
report one. What was genuinely lost keeps its own codes above, and they still
fire on that path.

Two work classes, one thread
----------------------------
The muse has exactly one thread, and two kinds of work want it:

* :data:`WORK_BOUNDARY` — a session the actor's position asked for, offered
  through :meth:`ThreadedMuseRunner.consider`. It ages: counsel about step 3 is
  worth less at step 40.
* :data:`WORK_COMPILATION` — a session that compiles the host's recalled
  material into counsel, offered through :meth:`ThreadedMuseRunner.compile`.
  Prompted by nothing in particular, so it does not age the same way.

**Boundary counsel wins, always.** One slot, one policy
(:meth:`ThreadedMuseRunner._admit`): boundary work displaces queued compilation,
compilation offered behind queued boundary work loses outright, and only the
newest compilation request is held. Every loss is a record — the alternative,
delaying counsel about where the actor *is* in order to finish compiling memory,
inverts the only priority this lane has. A host that never calls ``compile`` is
unaffected in every observable way: one class, one slot, the behaviour that
shipped before the class existed.

The ledger is a bounded window on the most recent transitions
(:data:`MAX_LEDGER`), so a long run cannot grow it without limit. Nothing
important can be crowded out of it: the **counters stay exact** whatever the
ledger drops, and the lane's terminal degradation lives in its own field rather
than in the ledger.

Observability is PULL-only
--------------------------
:meth:`ThreadedMuseRunner.snapshot`, :attr:`~ThreadedMuseRunner.degradations`
and :attr:`~ThreadedMuseRunner.counts` are folds of what already happened. There
is deliberately no ``on_degradation`` callback and no listener registry: a push
surface here would be the presence event stream claim c33 rules out, arriving by
the back door — and firing host code from the muse's thread is exactly how a
"never blocks the actor" promise gets quietly broken.

Advisory only — structurally
----------------------------
The runner drains :class:`~embodiment.muse.MuseInsight` values, which carry text
and provenance and nothing else. It builds no executor, constructs no tool and
holds no decision vocabulary in scope: it never imports :mod:`embodiment.loop`.
There is no path from anything here to a tool-call decision, and that is held by
the mechanism rather than by promise (`colleague#352
<https://github.com/agentculture/colleague/issues/352>`_). A host-supplied
thinking bench does not change that — see the next section for what carrying one
does and does not mean.

The tool bench is CARRIED, never owned (task t26)
-------------------------------------------------
:mod:`embodiment.muse` grew a thinking-tool seam in task t10 — a
:class:`~embodiment.muse.MuseToolBench` of schema, tool-carrying completion and
executor — and this module was the missing wire. It is the only thing that drives
the muse inside a live drive, so until now a bench could be reached only by a
harness building a :class:`~embodiment.muse.MuseLoop` by hand: the seam, the pad
(t12) and the workspace (t13) all existed and none of them was reachable from a
running drive (issue #30). ``tools`` and ``depth`` now pass straight through to
that loop.

*Straight through* is the whole of it, and each half is deliberate:

* **No second gate.** :func:`embodiment.muse._bench_for` decides whether a bench
  reaches the wire — top-level only, failing closed on a depth it cannot read,
  recording :data:`~embodiment.muse.DEGRADED_TOOLS_WITHHELD` when it withholds.
  This module re-implements none of that and compares ``depth`` to nothing; the
  withholding reaches a host exactly the way every other session-level code
  does, copied onto this ledger by :meth:`ThreadedMuseRunner._absorb`.
* **No ownership.** A bench is host state that may hold resources — the muse
  workspace holds a container — and this runner keeps no reference to one.
  :meth:`ThreadedMuseRunner.close` therefore *cannot* tear a bench down, which
  is the right answer twice over. Whoever wired it owns its lifetime, exactly as
  they own the injected ``complete`` seam this module has never closed either;
  and the join at teardown is **bounded** by design, so a session parked inside
  a model call can still be using the bench after ``close`` returns. Destroying
  it here would be a use-after-teardown the daemon-thread contract deliberately
  permits.
* **No inspection.** Nothing here reads a schema, a tool name, an argument or a
  result. A tool result is text the muse reads, and it reaches this module only
  as the narration of an insight; the tool authority boundary
  (:data:`~embodiment.muse.MUSE_TOOL_AUTHORITY`) is applied where the tools are.

What the runner does add is the **measurement**: ``counts["tool_rounds"]`` sums
the rounds every absorbed session spent, so *"the muse used its tools during this
drive"* is a number a host can read rather than something only the host's own
executor could see. Zero is a measurement — a tools-off lane genuinely spent
none — and not an absence.

Configuration is explicit
-------------------------
The muse's endpoint arrives already built, inside the injected ``complete``
seam, which a host resolves **by role name** from its own gateway contract.
This module has no model name, no address, no key and no sniffing: ``role`` is a
label recorded for provenance, never something to infer a capability from.

Stdlib only (constraint C1): ``collections``, ``dataclasses``, ``threading``,
``typing``.
"""

from __future__ import annotations

import math
import threading
from collections import deque
from dataclasses import dataclass, replace
from typing import Any, Callable, Iterable, Optional, cast

from embodiment.muse import (
    COUNSEL_KIND_DURABLE,
    COUNSEL_KIND_STEP,
    DEFAULT_STALE_LAG,
    MUSE_EXIT_DEGRADED,
    MuseCompleteFn,
    MuseControls,
    MuseDegradation,
    MuseInsight,
    MuseLoop,
    MuseOutcome,
    MuseToolBench,
    insight_lag,
    is_stale,
)
from embodiment.presence_engine import BoundaryContext, MuseComment

#: This module's archival, as data a host or a test can read (embodiment#53).
#: Declarative on purpose — see the module docstring. This is the lane
#: :mod:`embodiment.strategist_runner` cites, so "readable" is load-bearing.
ARCHIVED = (
    "archived 2026-08-03 (embodiment#53, deviations d2/d3): the muse runner left the "
    "shipped reference architecture and stays readable as the verbatim source "
    "embodiment.strategist_runner was copied from; supersedes claims c12 and c32"
)

__all__ = [
    # the archival marker (embodiment#53, deviations d2/d3)
    "ARCHIVED",
    # role + thread identity
    "MUSE_ROLE",
    "THREAD_NAME",
    # the DELIVERY vocabulary — what arrived, not what broke (task t5)
    "DELIVERY_TERMINAL",
    "DELIVERY_POINTS",
    "MAX_DELIVERIES",
    "MuseDelivery",
    # the transition vocabulary (C3)
    "DEGRADED_THREAD",
    "DEGRADED_WORKER",
    "DEGRADED_ENDPOINT",
    "DEGRADED_CLOSER",
    "DROPPED_STALE",
    "DROPPED_LATE",
    "DROPPED_OVERFLOW",
    "DROPPED_BOUNDARY",
    "DROPPED_COMPILATION_STARVED",
    "DROPPED_COUNSEL_DISPLACED",
    "RUNNER_CODES",
    # work-class labels
    "WORK_BOUNDARY",
    "WORK_COMPILATION",
    "WORK_CLASSES",
    "COMPILATION_REASON",
    # defaults
    "DEFAULT_MAX_PENDING",
    "DEFAULT_MAX_FAILED_SESSIONS",
    "DEFAULT_JOIN_TIMEOUT",
    "DEFAULT_POLL_INTERVAL",
    "MAX_LEDGER",
    # the runner
    "ThreadFactory",
    "Closer",
    "ThreadedMuseRunner",
]


# ── identity ──────────────────────────────────────────────────────────────────

#: The ROLE this runner drives, by name. Roles resolve by name from a host's own
#: capability contract — never by parsing a model name (there is no model name
#: anywhere in this module, on purpose).
MUSE_ROLE = "muse"

#: The one thread's name. Fixed so a host's stack dump or profiler names it.
THREAD_NAME = "embodiment-muse"


# ── the transition vocabulary (C3) ────────────────────────────────────────────

#: The worker thread could not be started; the lane never runs at all.
DEGRADED_THREAD = "muse-thread-unavailable"
#: The worker itself died on an unexpected exception. It should be unreachable —
#: :meth:`embodiment.muse.MuseLoop.think` never raises — so if it fires, the
#: record is the only thing standing between a bug and a silently absent mind.
DEGRADED_WORKER = "muse-worker-failed"
#: Consecutive thinking sessions failed outright; the lane stops dialling.
DEGRADED_ENDPOINT = "muse-endpoint-dead"
#: A host-wired teardown callable raised at :meth:`ThreadedMuseRunner.close`.
#: The lane is already stopping, so this never propagates — but a teardown that
#: did not happen is exactly the kind of loss C3 refuses to leave unsaid, and
#: the callable's own subject (a workspace, a socket, a file) is state the host
#: now has to deal with by hand.
DEGRADED_CLOSER = "muse-closer-failed"
#: An insight fell too far behind the actor loop to be worth delivering.
DROPPED_STALE = "muse-insight-stale"
#: An insight arrived (or was still buffered) after the runner closed.
DROPPED_LATE = "muse-insight-late"
#: The drain buffer was full; the oldest insight was discarded to make room.
DROPPED_OVERFLOW = "muse-insight-overflow"
#: A queued boundary was replaced by a newer one before it was ever thought about.
DROPPED_BOUNDARY = "muse-boundary-superseded"
#: A background compilation work item never reached the muse's one thread:
#: boundary counsel outranks it, only the newest compilation request is held,
#: and a lane that closes takes whatever is still queued with it. Recorded in
#: :meth:`ThreadedMuseRunner._admit` and :meth:`ThreadedMuseRunner.close`.
DROPPED_COMPILATION_STARVED = "muse-compilation-starved"
#: Boundary counsel was discarded from a full drain buffer to make room for
#: background compilation output — the LOWER-priority work class evicting the
#: higher one. Distinct from :data:`DROPPED_OVERFLOW`, which is same-class (or
#: outranking-class) backpressure and means "I am thinking faster than the actor
#: drains"; this one is a priority INVERSION, and its remediation is different:
#: raise ``max_pending`` or compile less. Recorded in
#: :meth:`ThreadedMuseRunner._deliver`.
DROPPED_COUNSEL_DISPLACED = "muse-counsel-displaced"

#: The complete set of DEGRADATION codes this module can record. Session-level
#: codes come through verbatim from :mod:`embodiment.muse` — the thinking-failure
#: family, joined in task t26 by
#: :data:`~embodiment.muse.DEGRADED_TOOLS_WITHHELD`, which a bench wired below
#: the top level produces. This runner mints no code outside these ten, and
#: re-implements none of the decisions behind the ones it relays. The delivery
#: vocabulary (:data:`DELIVERY_POINTS`) is deliberately not in here — see
#: :class:`MuseDelivery` for why a delivery is not a degradation.
RUNNER_CODES = (
    DEGRADED_THREAD,
    DEGRADED_WORKER,
    DEGRADED_ENDPOINT,
    DEGRADED_CLOSER,
    DROPPED_STALE,
    DROPPED_LATE,
    DROPPED_OVERFLOW,
    DROPPED_BOUNDARY,
    DROPPED_COMPILATION_STARVED,
    DROPPED_COUNSEL_DISPLACED,
)


# ── the delivery vocabulary (task t5) — what ARRIVED, not what broke ──────────

#: The drive's LAST drain: the beat whose job is delivery rather than presence
#: (:meth:`embodiment.presence_engine.PresenceEngine.on_terminal_boundary`,
#: fired once at drive end by :func:`embodiment.loop.run`). Named on the record
#: rather than implied by it, so a record copied out of its container into a
#: host's own artifact still says which beat produced it.
DELIVERY_TERMINAL = "terminal"

#: Every drain point that mints a :class:`MuseDelivery`. Exhaustive, and
#: declared so a host may branch over it — and so
#: ``tests/test_ledger.py``'s ``DELIVERY_PROVOKERS`` can require a real
#: producing path for each entry. That requirement is embodiment#18's rule,
#: which was never about degradation vocabulary specifically: a declared
#: constant nothing produces is dead whichever stream it belongs to.
DELIVERY_POINTS = (DELIVERY_TERMINAL,)


# ── defaults ──────────────────────────────────────────────────────────────────

#: How many undrained insights the buffer holds before discarding the oldest.
DEFAULT_MAX_PENDING = 32
#: Consecutive failed sessions tolerated before the lane stops dialling. ``1``
#: mirrors the pump's own rule for a raising seam: a dead endpoint is not
#: re-dialled at every step. A host that expects a flaky link raises it.
DEFAULT_MAX_FAILED_SESSIONS = 1
#: Bound on :meth:`ThreadedMuseRunner.close`'s join. Teardown never hangs.
DEFAULT_JOIN_TIMEOUT = 1.0
#: The worker's poll-wake bound. Correctness never depends on it — the wake
#: event does the work — so it is a safety net for a missed wakeup, not a clock.
DEFAULT_POLL_INTERVAL = 0.5
#: How many of the most recent transitions the ledger keeps. The counters stay
#: exact past it, and the terminal degradation is held separately.
MAX_LEDGER = 100
#: How many delivery records to keep. One drive produces exactly one, so this
#: only binds a runner deliberately reused across drives; the counters
#: (``terminal_drains`` / ``insights_delivered_terminal``) stay exact past it,
#: the same discipline :data:`MAX_LEDGER` already holds.
MAX_DELIVERIES = 100

#: Cap on one record's reason text, mirroring :mod:`embodiment.muse`.
_MAX_REASON_LEN = 500

#: Work-class labels for the muse thread's two kinds of work. There is ONE
#: thread, so the two classes contend for it, and the contention has a policy:
#: boundary counsel outranks background compilation, always.
#:
#: ``boundary`` — a session the actor's own position asked for, offered through
#: :meth:`ThreadedMuseRunner.consider`. Time-sensitive: it is counsel about a
#: position the actor is at *now*, and it ages by loop distance.
WORK_BOUNDARY = "boundary"
#: ``compilation`` — a session that compiles the host's recalled material into
#: counsel with no boundary prompting it, offered through
#: :meth:`ThreadedMuseRunner.compile`. Anchored to no position, so it yields the
#: thread to boundary work rather than delaying it.
WORK_COMPILATION = "compilation"
#: The complete set of work classes. :meth:`ThreadedMuseRunner._offer` branches
#: on it, so a label outside it is refused rather than silently scheduled.
WORK_CLASSES = (WORK_BOUNDARY, WORK_COMPILATION)

#: The cadence reason a compilation work item's synthetic boundary carries, so
#: an insight compiled in the background is traceable to the work class that
#: produced it rather than reading as counsel the actor asked for.
COMPILATION_REASON = "background compilation of recalled material"

#: Builds the worker thread. Injected so a test can assert a museless run
#: creates none, and so a host with its own thread policy can supply one.
ThreadFactory = Callable[..., Any]

#: One host-owned teardown, tied to this lane's close. A zero-argument callable
#: and **nothing more** — the runner never inspects it, never learns what it
#: closes, and imports nothing to accommodate it.
#:
#: It exists because a host that gives the muse a tool has two lifetimes that
#: must coincide: the thinking thread's, and whatever that tool holds open. The
#: muse's workspace (:meth:`embodiment.workspace.MuseWorkspace.close`) is the
#: motivating case — destroying it early breaks a session still in flight,
#: destroying it late leaks a container — and this is the seam that lets a host
#: say "when the muse lane ends, so does that" in one argument, without
#: :mod:`embodiment.muse_runner` growing a dependency on
#: :mod:`embodiment.workspace` or on ``headspace``. Ownership stays where
#: construction is; only the *timing* is delegated here.
Closer = Callable[[], Any]


def _bounded_join(thread: Any, *, timeout: float = DEFAULT_JOIN_TIMEOUT) -> None:
    """Join *thread* with a bounded timeout — never hangs.

    The discipline is ``colleague/realtime.py``'s, inherited rather than
    imported: every pump thread is a daemon stopped through a
    :class:`threading.Event` and reaped through a bounded join, so a session
    parked inside a model call that cannot be interrupted delays teardown by at
    most *timeout* and then stops mattering (the process can still exit; the
    thread is a daemon). Idempotent-safe: joining an already-finished thread
    returns immediately, and joining the CURRENT thread is skipped rather than
    raising.
    """
    if thread is None or thread is threading.current_thread():
        return
    try:
        if thread.is_alive():
            thread.join(timeout=timeout)
    except RuntimeError:  # pragma: no cover - a thread object that refuses a join
        return


def _coerce_int(value: Any, default: int = 0) -> int:
    """Best-effort ``int``; anything uncoercible falls back to *default*."""
    try:
        return int(value)
    except Exception:  # a junk count is a default, never a crash
        return default


def _read(obj: Any, name: str, default: Any = None) -> Any:
    """``getattr`` that cannot raise — a hostile property reads as absent.

    Every boundary field this module touches goes through here, because
    ``consider`` runs on the ACTOR's thread: an exception raised reading a
    host's object would be an exception in the actor loop's main path, which is
    the one thing a presence layer may never do.
    """
    try:
        return getattr(obj, name, default)
    except Exception:  # noqa: BLE001  # an unreadable attribute is simply absent
        return default


def _step_of(boundary: Any) -> int:
    """The actor step a boundary carries. Never raises."""
    return _coerce_int(_read(boundary, "step_count", 0))


def _kind_of(boundary: Any) -> str:
    """The boundary kind, for a record's reason text. Never raises."""
    kind = _read(boundary, "kind", "")
    return kind if isinstance(kind, str) else ""


def _carry(boundary: BoundaryContext) -> BoundaryContext:
    """Snapshot *boundary* for the crossing onto the worker thread.

    Everything on a :class:`~embodiment.presence_engine.BoundaryContext` is
    already a value the pump read on the actor's thread — except ``history``,
    which is a live list the host may still be appending to. Copying it means
    the muse never iterates a list under concurrent mutation. A boundary that
    cannot be copied is passed through unchanged rather than dropped.
    """
    history = _read(boundary, "history")
    if not isinstance(history, list):
        return boundary
    try:
        return cast(BoundaryContext, replace(boundary, history=list(history)))
    except Exception:  # an uncopyable boundary still gets thought about
        return boundary


def _insight_id(insight: Any) -> str:
    """A stable id for one delivered insight. Never raises.

    The key is the one the muse already stamps, not a new invention:
    :attr:`~embodiment.muse.MuseOrigin.session` is its monotonic thinking
    session and ``turn_index`` the turn inside that session, which together
    name exactly one produced thought. Unique within a runner (one runner drives
    one :class:`~embodiment.muse.MuseLoop`, which mints the session numbers),
    which is the scope a delivery record is read in.

    An unreadable insight yields ``s0t0`` rather than an exception: this runs
    on the actor's thread, at the last beat of a drive, and an id derivation
    that could raise there would be a failure path invented by an observability
    surface.
    """
    origin = _read(insight, "origin")
    session = _coerce_int(_read(origin, "session", 0))
    turn = _coerce_int(_read(insight, "turn_index", 0))
    return f"s{session}t{turn}"


@dataclass(frozen=True)
class MuseDelivery:
    """What ONE drain actually handed the actor — and NOT a degradation.

    The cycle this record belongs to claims that counsel which used to be
    stranded at close now reaches the actor: issue #17 measured one late drop
    per run in 4 of 4 runs, the terminal boundary stopped starting the session
    that stranded, and drive end became the single trigger that fires it. A
    delivery path producing no observable record would make that the one place
    the fix is claimed and cannot be checked (constraint C3).

    **Zero is recorded, never skipped.** *"The terminal drain ran and delivered
    nothing"* and *"the terminal drain never ran"* are different facts with
    different remedies; a record that only appeared when something arrived
    would collapse them. An absent record means the beat did not happen.

    **Why this is not a ``DROPPED_*`` code.** The runner's degradation
    vocabulary answers *"what went wrong?"*, and
    :mod:`embodiment.ledger` refuses to fold a budget exit into it precisely
    because "folding it in would make the stream claim breakage that did not
    happen". A terminal drain delivering three insights is this lane working;
    delivering none is the muse having had nothing left. Minting a degradation
    code for either would make every healthy run report one, and a stream that
    cries wolf on success is worth less than no stream. Counsel that genuinely
    IS lost keeps its codes — :data:`DROPPED_STALE`, :data:`DROPPED_LATE`,
    :data:`DROPPED_OVERFLOW`, :data:`DROPPED_BOUNDARY` — and they still fire on
    this path.

    Fields
    ------
    point:
        Which drain minted this, from :data:`DELIVERY_POINTS`.
    count:
        How many insights the drain handed back. What the ACTOR received, after
        stale counsel was dropped — never what the muse produced.
    insight_ids:
        Those insights' ids, in delivery order (see :func:`_insight_id`). The
        count is not a bare number a reader has to trust: the ids say which
        thoughts it counted, and they tie back to the session and turn that
        produced each one.
    step_index:
        The acting loop's step the drain read at.
    """

    point: str = DELIVERY_TERMINAL
    count: int = 0
    insight_ids: tuple[str, ...] = ()
    step_index: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "point": self.point,
            "count": self.count,
            "insight_ids": list(self.insight_ids),
            "step_index": self.step_index,
        }


@dataclass(frozen=True)
class _Work:
    """One unit of muse work: what to think about, and which class it belongs to.

    Private on purpose. The two work classes are a scheduling fact about the one
    thread, not a shape a host hands in: a host offers work through
    :meth:`ThreadedMuseRunner.consider` or :meth:`ThreadedMuseRunner.compile`
    and the runner labels it. Frozen so a queued item cannot be edited after the
    priority decision that admitted it.
    """

    work_class: str
    boundary: BoundaryContext
    recall_bundle: Any = None


class ThreadedMuseRunner:
    """Run ONE :class:`~embodiment.muse.MuseLoop` on one daemon thread.

    Satisfies the pump's drain-shaped
    :class:`~embodiment.presence_engine.MuseSeam`: ``consider`` offers a
    boundary and returns, ``drain`` collects whatever finished, ``degradation``
    reports the lane having stopped. None of the three blocks, and none of them
    raises. :meth:`compile` is the fourth, host-facing verb — the second work
    class, which the pump does not drive and which yields to ``consider``.

    Args:
        complete: the injected tools-off thinking seam
            (:data:`~embodiment.muse.MuseCompleteFn`), already carrying its own
            endpoint. This module never infers a model, a role or an address.
            It is the muse's floor: it runs every session no bench reaches the
            wire on, and it stays required when one is wired.
        role: the role NAME this lane drives, recorded for provenance. It names
            who is thinking, never what may be inferred from it.
        controls: the thinking loop's turn budget and caps.
        system: OPTIONAL host framing, appended to the muse's authority boundary
            (never substituted for it).
        clock: the ONLY source of a latency measurement, handed to the thinking
            loop. Absent, every ``latency`` stays ``None`` rather than a
            fabricated zero. A runner owns a thread, so it may legitimately own
            a clock too — but only if the host gives it one.
        tools: OPTIONAL :class:`~embodiment.muse.MuseToolBench` of THINKING
            tools, handed straight to the loop and never kept here (task t26).
            ``None`` — the default — is the tools-off lane, and on that path the
            constructed loop and every prompt it sends are what they were before
            this argument existed. The bench carries the second seam that may
            talk to a network: its own tool-carrying completion.
        depth: where this runner's muse sits, passed through to the loop's
            single depth gate. ``0`` is the top-level muse, the only one that
            may hold tools this cycle; anything else — including a value that
            cannot be read as an integer — withholds the bench and records
            :data:`~embodiment.muse.DEGRADED_TOOLS_WITHHELD`, which
            :meth:`_absorb` copies onto this lane's ledger. The decision is
            :func:`embodiment.muse._bench_for`'s alone; nothing here repeats it.
        max_pending: how many undrained insights to hold before discarding the
            oldest (and recording the discard).
        max_lag: how many actor steps an insight may fall behind before
            :meth:`drain` drops it as stale. The consumer's policy;
            :data:`~embodiment.muse.DEFAULT_STALE_LAG` is only a default.
        max_failed_sessions: consecutive failed thinking sessions tolerated
            before the lane stops dialling.
        join_timeout: the default bound on :meth:`close`'s join.
        poll_interval: the worker's poll-wake bound (a safety net, not a clock).
        thread_factory: builds the worker thread; :class:`threading.Thread` by
            default. Called with ``target``, ``name`` and ``daemon`` keywords.
        closers: host-owned teardowns to run once at :meth:`close`, in order,
            after the bounded join and after the late-drop accounting — see
            :data:`Closer`. Each must **bound itself**; the runner deliberately
            does not wrap them in a second bound, because an outer timeout
            would cut off an inner one before it could record what it left
            behind. The one embodiment ships,
            :meth:`embodiment.workspace.MuseWorkspace.close`, is bounded, and
            the default ``()`` leaves every existing host byte-identical.

    Lifecycle::

        with ThreadedMuseRunner(complete) as muse:      # no thread yet
            outcome = run(complete, task, executor=…, presence=PresenceEngine(
                io=io, muse=muse))                       # thread starts on the
                                                         # first boundary
        # closed: stop signalled, join bounded, undrained insights recorded

    The runner must not outlive the run: :meth:`close` is idempotent, bounded,
    and safe to call from anywhere except the worker thread itself.
    """

    def __init__(
        self,
        complete: MuseCompleteFn,
        *,
        role: str = MUSE_ROLE,
        controls: Optional[MuseControls] = None,
        system: Optional[str] = None,
        clock: Optional[Callable[[], float]] = None,
        tools: Optional[MuseToolBench] = None,
        depth: Any = 0,
        max_pending: int = DEFAULT_MAX_PENDING,
        max_lag: int = DEFAULT_STALE_LAG,
        max_failed_sessions: int = DEFAULT_MAX_FAILED_SESSIONS,
        join_timeout: float = DEFAULT_JOIN_TIMEOUT,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        thread_factory: Optional[ThreadFactory] = None,
        recall_bundle: Any = None,
        closers: Iterable[Closer] = (),
    ) -> None:
        self._role = str(role or MUSE_ROLE)
        # Materialised at construction, not at close: a generator handed here
        # would be consumed by an earlier close and silently empty at the one
        # that matters. Deliberately NOT wrapped in a try — a `closers` that is
        # not iterable is a wiring mistake, and failing at construction beats
        # discovering at teardown that nothing was ever going to be closed.
        self._closers: tuple[Closer, ...] = tuple(closers)
        # The recall-context material this runner's muse compiles from, fetched
        # ONCE by the host for the whole work item rather than per boundary.
        # That granularity is the honest one: a work item has one recalled
        # context, and it is also what keeps this module IO-free — the runner
        # never fetches, so the muse thread never blocks on a store.
        self._recall_bundle = recall_bundle
        # The citation surface, seeded from the bundle AT CONSTRUCTION rather
        # than accumulated as sessions finish. That ordering is deliberate and
        # was a measured correction: the muse is a background thread, so
        # whether any session had completed by the time a host built its report
        # varied run to run (3 sessions in one run, 0 in the next on the same
        # input). An accumulate-on-absorb surface is therefore not wrong so
        # much as NON-DETERMINISTIC, which is worse — a provenance field that
        # is sometimes empty for timing reasons teaches a reader to distrust it
        # when it is full.
        #
        # What this therefore means, stated rather than implied: the material
        # the muse was GIVEN for this work item, not proof it finished reading
        # it. That is the same standard ``links`` already holds itself to —
        # lifecycle documents it as "what the agent knew when it acted" — and
        # ``counts["sessions_completed"]`` is right there for a reader who
        # needs the stronger fact. :attr:`MuseOutcome.compiled_from` remains
        # per-session and exact.
        # A dict keeps insertion order while deduping.
        self._compiled_from: dict[str, None] = {}
        for record_id in getattr(recall_bundle, "record_ids", ()) or ():
            self._compiled_from[str(record_id)] = None
        # ONE loop instance, driven by ONE thread, one session at a time — the
        # protocol embodiment.muse documents for exactly this consumer.
        #
        # ``tools`` and ``depth`` are handed over and NOT stored (task t26).
        # That is the ownership statement made structurally rather than in
        # prose: there is no attribute here holding a host's bench, so nothing
        # in this module — ``close`` included — can reach one to inspect it,
        # call it or tear it down. With no bench this is byte-for-byte the loop
        # the pre-t26 runner built: ``tools=None`` and ``depth=0`` are
        # ``MuseLoop``'s own defaults, and its depth gate reads ``depth`` only
        # when a bench exists to withhold.
        self._loop = MuseLoop(
            complete,
            controls=controls,
            system=system,
            sink=self._deliver,
            clock=clock,
            tools=tools,
            depth=depth,
        )
        self._max_lag = max(0, _coerce_int(max_lag, DEFAULT_STALE_LAG))
        self._max_failed = max(1, _coerce_int(max_failed_sessions, DEFAULT_MAX_FAILED_SESSIONS))
        self._join_timeout = join_timeout
        self._poll_interval = poll_interval
        self._thread_factory: ThreadFactory = thread_factory or threading.Thread

        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._idle = threading.Event()
        self._idle.set()

        self._thread: Any = None
        self._closed = False
        self._degradation: Optional[str] = None
        self._pending: Optional[_Work] = None
        # Each buffered insight is held with the work class that produced it, so
        # an eviction can say WHOSE thinking was discarded for WHOSE. Without
        # that pairing a full buffer is one undifferentiated fact; with it, the
        # priority inversion (compilation evicting boundary counsel) is a
        # distinct, separately actionable record.
        self._ready: deque[tuple[str, MuseInsight]] = deque(
            maxlen=max(1, _coerce_int(max_pending, 1))
        )
        self._ledger: deque[MuseDegradation] = deque(maxlen=MAX_LEDGER)
        # The DELIVERY ledger — a separate stream from ``_ledger`` because a
        # delivery is not a degradation (see :class:`MuseDelivery`). Bounded on
        # the same discipline; the counters below stay exact past it.
        self._deliveries: deque[MuseDelivery] = deque(maxlen=max(1, MAX_DELIVERIES))
        self._failures = 0
        self._observed_step = 0
        #: The class of the session currently in flight. Written by ``_take``
        #: and read by ``_deliver``, both on the worker thread, both under lock.
        self._current_class = WORK_BOUNDARY
        self._counts = {
            "sessions_started": 0,
            "sessions_completed": 0,
            "insights_delivered": 0,
            "insights_dropped_stale": 0,
            "insights_dropped_late": 0,
            "insights_dropped_overflow": 0,
            "boundaries_superseded": 0,
            "compilation_starved": 0,
            "counsel_displaced": 0,
            "degradations_recorded": 0,
            # How many TOOL rounds the absorbed sessions spent between them
            # (task t26). A tools-off lane spends none, and that zero is a
            # measurement rather than an absence — the same standard
            # :attr:`~embodiment.muse.MuseOutcome.tool_rounds` already holds
            # itself to. Without it, "the muse used its tools in this drive"
            # would be observable only to whoever owns the executor.
            "tool_rounds": 0,
            # The terminal beat, counted separately from every other drain: how
            # many ran, and how much counsel they carried. Both stay exact past
            # the bounded delivery ledger, and a zero here is a measurement —
            # the drain ran and had nothing — never an absence.
            "terminal_drains": 0,
            "insights_delivered_terminal": 0,
        }
        # How many sessions each work class actually got. Zero here is a real
        # measurement, not an absence: a host that sees `compilation_starved`
        # rising against `compilation: 0` knows its background lane never ran
        # once, which the drop records alone do not say.
        self._work_started: dict[str, int] = dict.fromkeys(WORK_CLASSES, 0)
        # Per-kind delivery counters (task t3).
        self._kind_delivered: dict[str, int] = {}
        self._kind_dropped: dict[str, int] = {}
        # Relative latency: muse turn times vs. loop step times. The muse side
        # fills itself from each finished session (see :meth:`_absorb`); the
        # loop side can only come from the host, which is the only party that
        # knows how long its own steps took (see :meth:`note_loop_step`).
        self._muse_turn_times: list[float] = []
        self._loop_step_times: list[float] = []

    # ── the drain-shaped seam ────────────────────────────────────────────────
    def consider(self, boundary: Optional[BoundaryContext]) -> None:
        """Offer one boundary to think about, then return. Never blocks or raises.

        At most one boundary is ever queued: a boundary arriving while a session
        is in flight REPLACES any boundary still waiting, so the muse always
        thinks about the most recent position rather than working through a
        backlog of stale ones. The displaced boundary is recorded, never
        silently dropped.
        """
        if boundary is None:
            return
        self._offer(_Work(WORK_BOUNDARY, _carry(boundary), self._recall_bundle))

    def compile(self, *, recall_bundle: Any = None, step_count: int = 0) -> None:
        """Offer one BACKGROUND COMPILATION session, then return. Never blocks or raises.

        The muse's second work class: a session with no boundary prompting it,
        whose job is to compile the host's recalled material
        (:class:`~embodiment.recall_bundle.RecallBundle`) into counsel the actor
        can read later. ``eidetic`` fetches, this module never does, and the
        muse compiles — so a host that has recalled something and wants it
        chewed on offers it here instead of waiting for the next boundary.

        Compilation **yields to boundary counsel**, which is the whole reason
        the two classes are named. The muse has one thread; counsel about where
        the actor is right now ages, and background compilation does not, so
        boundary work takes the slot and a compilation item that loses it is
        recorded as :data:`DROPPED_COMPILATION_STARVED` rather than delaying
        anything. At most one compilation item is queued: a newer request
        replaces an older one, exactly as boundaries supersede boundaries.

        Args:
            recall_bundle: the material to compile. Defaults to the bundle the
                runner was constructed with, so a host that fetched once for the
                whole work item needs no argument.
            step_count: the actor's step to stamp the session's provenance with.
                Defaults to the highest step the runner has observed, so
                compiled counsel is judged for staleness against where the actor
                actually is rather than against step zero.
        """
        with self._lock:
            step = max(self._observed_step, _coerce_int(step_count))
        boundary = BoundaryContext(
            kind=WORK_COMPILATION,
            step_count=step,
            reason=COMPILATION_REASON,
        )
        bundle = self._recall_bundle if recall_bundle is None else recall_bundle
        self._offer(_Work(WORK_COMPILATION, boundary, bundle))

    def _offer(self, work: _Work) -> None:
        """Queue one work item under the class priority. Never blocks or raises.

        At most one item is ever queued, across BOTH classes: the muse has one
        thread, and a backlog of stale positions is exactly what the single slot
        exists to prevent. Whatever loses the slot is recorded, never silently
        dropped — :meth:`_admit` holds that policy.
        """
        if work.work_class not in WORK_CLASSES:  # a label nobody scheduled
            return
        with self._lock:
            if self._closed or self._degradation is not None:
                return
            if not self._admit(work):
                return
            self._pending = work
            self._observed_step = max(self._observed_step, _step_of(work.boundary))
            self._idle.clear()
        if not self.start():
            # No thread means nothing will ever pick this up; say so rather than
            # leaving work queued against a lane that does not exist.
            with self._lock:
                self._pending = None
                self._idle.set()
            return
        self._wake.set()

    def _admit(self, work: _Work) -> bool:
        """Decide whether *work* takes the one slot, recording what loses it.

        Call with the lock held. The policy, in one place:

        * an empty slot admits anything;
        * boundary counsel displaces whatever is queued — another boundary is
          ``superseded`` (the position moved on), a compilation item is
          ``starved`` (it never got the thread);
        * compilation offered behind queued boundary counsel loses outright,
          because delaying counsel about the actor's current position to compile
          memory would invert the only priority this lane has;
        * compilation offered behind queued compilation replaces it, so the
          freshest material is the one that gets thought about.
        """
        queued = self._pending
        if queued is None:
            return True
        if work.work_class == WORK_BOUNDARY:
            if queued.work_class == WORK_BOUNDARY:
                self._record(
                    DROPPED_BOUNDARY,
                    f"boundary {_kind_of(queued.boundary)!r} superseded before it was "
                    "thought about",
                    step_index=_step_of(queued.boundary),
                )
                self._counts["boundaries_superseded"] += 1
            else:
                self._starve(queued, "boundary counsel took the muse's one thread")
            return True
        if queued.work_class == WORK_BOUNDARY:
            self._starve(work, "boundary counsel was already queued and outranks it")
            return False
        self._starve(queued, "a newer compilation request replaced it")
        return True

    def _starve(self, work: _Work, why: str) -> None:
        """Record one compilation item that will never run. Call with the lock held."""
        self._counts["compilation_starved"] += 1
        self._record(
            DROPPED_COMPILATION_STARVED,
            f"background compilation never reached the muse's thread: {why}",
            step_index=_step_of(work.boundary),
        )

    def drain(self, *, step_count: int = 0) -> list[MuseComment]:
        """Return whatever insights are ready NOW. Never blocks or raises.

        An empty list is the normal case — the muse thinks on its own clock and
        an actor loop must never wait on it. *step_count* is the actor's current
        step; step-sensitive counsel that has fallen more than ``max_lag`` steps
        behind is dropped, and each drop is recorded (C3).

        **Durable counsel is never dropped for loop-distance staleness alone** —
        it survives to the next boundary or synthesis. Only step-sensitive
        counsel ages by loop distance.
        """
        with self._lock:
            self._observed_step = max(self._observed_step, _coerce_int(step_count))
            current = self._observed_step
            ready = list(self._ready)
            self._ready.clear()
            kept: list[MuseComment] = []
            for _work_class, insight in ready:
                kind = getattr(insight, "kind", COUNSEL_KIND_DURABLE)
                if kind == COUNSEL_KIND_STEP and is_stale(
                    insight, step_count=current, max_lag=self._max_lag
                ):
                    self._counts["insights_dropped_stale"] += 1
                    self._kind_dropped.setdefault(kind, 0)
                    self._kind_dropped[kind] += 1
                    self._record(
                        DROPPED_STALE,
                        f"insight about step {insight.origin.step_count} read at step "
                        f"{current} (lag {insight_lag(insight, step_count=current)} > "
                        f"{self._max_lag})",
                        step_index=insight.origin.step_count,
                        model_turns=insight.turn_index,
                    )
                    continue
                kept.append(insight)
                self._kind_delivered.setdefault(kind, 0)
                self._kind_delivered[kind] += 1
            self._counts["insights_delivered"] += len(kept)
            return kept

    def drain_terminal(self, *, step_count: int = 0) -> list[MuseComment]:
        """Drain at the LAST beat, and RECORD what that drain delivered (task t5).

        Identical to :meth:`drain` in everything it hands back — same staleness
        policy, same records, same non-blocking, non-raising contract — and it
        adds exactly one thing: a :class:`MuseDelivery` naming how much counsel
        this beat carried and which insights it was. **Even when that is
        nothing**, because "the terminal drain ran and delivered nothing" is a
        different fact from "the terminal drain never ran", and only one of them
        leaves a record.

        Why it is a separate verb rather than a flag on :meth:`drain`
        ------------------------------------------------------------
        "This drain is the last one" is a fact about the ACTOR's lifecycle, and
        the runner has no view of that — the same reason
        :meth:`note_loop_step` exists. So it arrives from the one caller that
        knows: the pump's terminal beat
        (:meth:`embodiment.presence_engine.PresenceEngine.on_terminal_boundary`,
        fired once at drive end by :func:`embodiment.loop.run`). A separate verb
        keeps the three-member :class:`~embodiment.presence_engine.MuseSeam`
        protocol unchanged, so every seam a host already wrote is untouched: the
        pump probes for this verb by name and falls back to :meth:`drain`, the
        same optional-capability shape the loop uses to probe a presence sink
        for ``on_terminal_boundary``.

        Deviation ``d1`` holds here without exception: this collects what is
        ready *now*. A muse still mid-thought is not waited for, and the record
        then honestly says nothing arrived.
        """
        delivered = self.drain(step_count=step_count)
        with self._lock:
            self._counts["terminal_drains"] += 1
            self._counts["insights_delivered_terminal"] += len(delivered)
            self._deliveries.append(
                MuseDelivery(
                    point=DELIVERY_TERMINAL,
                    count=len(delivered),
                    insight_ids=tuple(_insight_id(item) for item in delivered),
                    step_index=self._observed_step,
                )
            )
        return delivered

    def degradation(self) -> Optional[str]:
        """Why this lane stopped thinking, or ``None`` while it is healthy."""
        with self._lock:
            return self._degradation

    # ── lifecycle ────────────────────────────────────────────────────────────
    def start(self) -> bool:
        """Start the worker thread if it is not running. Idempotent; never raises.

        Returns ``True`` when a live thread exists afterwards. A thread that
        cannot be started degrades the lane permanently, visibly and quietly —
        the host's run continues without a second mind, which is what the
        museless default already proves works.
        """
        with self._lock:
            if self._closed or self._degradation is not None:
                return False
            if self._thread is not None:
                return True
            try:
                thread = self._thread_factory(target=self._work, name=THREAD_NAME, daemon=True)
                thread.start()
            except Exception as exc:  # no thread is a degradation, not a crash
                self._degrade(DEGRADED_THREAD, f"{type(exc).__name__}: {exc}")
                return False
            self._thread = thread
            return True

    def close(self, *, timeout: Optional[float] = None) -> None:
        """Stop the lane: signal, bounded join, account for what is left, close what the host wired.

        Idempotent and safe to call from any thread but the worker's. Insights
        still buffered — and any produced after this returns — have nowhere to
        go, so each is recorded as a late drop (C3) rather than vanishing.

        **The order is load-bearing.** Closers run *last*, after the bounded
        join, because a closer's subject may still be in use by a session the
        join is waiting on: tearing a muse's workspace down before its thinking
        thread has stopped would break a command in flight rather than clean up
        after it. They also run after the late-drop accounting, so a closer
        reading :attr:`counts` sees a finished lane rather than one mid-close.

        **What "never hangs" covers.** The join is bounded, and a session parked
        inside an uninterruptible model call is simply left to the daemon
        thread — that part is unconditional. A ``closers`` entry is the host's
        own code on the host's own budget: the runner runs each exactly once
        and lets none of them raise, but it does not bound them (see
        :data:`Closer` for why a second bound would do harm). With no closers
        wired — the default — close is bounded by ``join_timeout`` alone.
        """
        with self._lock:
            first = not self._closed
            self._closed = True
            thread = self._thread
            stranded = list(self._ready)
            self._ready.clear()
            queued = self._pending
            self._pending = None
            # A compilation item queued when the lane stops never gets the
            # thread, and "the runner closed first" is as real a starvation as
            # losing the slot to boundary counsel (C3: no silent loss).
            if first and queued is not None and queued.work_class == WORK_COMPILATION:
                self._starve(queued, "the runner closed before its turn came")
        self._stop.set()
        self._wake.set()
        _bounded_join(thread, timeout=self._join_timeout if timeout is None else timeout)
        with self._lock:
            self._idle.set()
            if first:
                for _work_class, insight in stranded:
                    self._drop_late(insight, "undrained when the runner closed")
        if first:
            self._run_closers()

    def _run_closers(self) -> None:
        """Run each host-wired teardown once. Never raises; a failure is recorded."""
        for closer in self._closers:
            try:
                closer()
            except Exception as exc:  # noqa: BLE001  # a close path never raises
                with self._lock:
                    self._record(
                        DEGRADED_CLOSER,
                        f"a teardown wired to this lane's close raised and did not "
                        f"finish: {type(exc).__name__}: {exc}",
                    )

    def wait_idle(self, timeout: float) -> bool:
        """Block until no session is in flight and none is queued.

        A bounded wait on a real condition, for orderly shutdown and for tests —
        which is why the concurrency tests need no sleeps. **The actor loop must
        never call this on its hot path**: waiting on the muse is the one thing
        deviation d1 exists to avoid. Returns ``False`` on timeout.
        """
        return self._idle.wait(timeout)

    def __enter__(self) -> "ThreadedMuseRunner":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    # ── pull-only observability ──────────────────────────────────────────────
    @property
    def role(self) -> str:
        """The role NAME this lane drives — provenance, never a capability."""
        return self._role

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def thread_started(self) -> bool:
        """Whether a worker thread was ever created. A museless run: never."""
        return self._thread is not None

    @property
    def compiled_from(self) -> tuple[str, ...]:
        """The recalled material this lane was given, in order, each once.

        Read by the continuity lifecycle at the memory boundary so a durable
        record ``links`` to the material its counsel was compiled from. Empty
        when no recall bundle was supplied, or when the bundle cited nothing —
        reported as empty rather than absent, because "compiled from nothing"
        and "no muse ran" are different facts and a host can tell them apart
        through :attr:`counts`.

        Deterministic by construction: see the note in ``__init__`` for why
        this is seeded from the bundle instead of accumulated as sessions
        complete, and for exactly how strong a claim it is.
        """
        with self._lock:
            return tuple(self._compiled_from)

    @property
    def degradations(self) -> list[MuseDegradation]:
        """The most recent transitions, at most :data:`MAX_LEDGER` of them."""
        with self._lock:
            return list(self._ledger)

    @property
    def deliveries(self) -> list[MuseDelivery]:
        """What the terminal drains delivered — a SEPARATE stream from degradations.

        Empty means no terminal drain ran; a record with ``count == 0`` means
        one ran and had nothing to hand over. See :class:`MuseDelivery` for why
        this is not folded into :attr:`degradations` (or into
        :mod:`embodiment.ledger`, which answers a different question).
        """
        with self._lock:
            return list(self._deliveries)

    @property
    def counts(self) -> dict[str, int]:
        """Exact counters — never truncated, even when the ledger is."""
        with self._lock:
            return dict(self._counts)

    def snapshot(self) -> dict[str, Any]:
        """A PULL-ONLY fold of what already happened. Returns copies."""
        with self._lock:
            return {
                "role": self._role,
                "thread_started": self._thread is not None,
                "closed": self._closed,
                "degradation": self._degradation,
                "counts": dict(self._counts),
                "degradations": list(self._ledger),
                "kind_delivered": dict(self._kind_delivered),
                "kind_dropped": dict(self._kind_dropped),
                "relative_latency": self._relative_latency(),
                # How many sessions each work class got. Both classes are always
                # present, because a scheduled class that ran zero times is a
                # measurement — unlike a degradation code, which reports nothing
                # rather than a zero when it never fired.
                "work_started": dict(self._work_started),
                # The citation surface, reported so a host's own artifact can
                # show which remembered records its counsel was compiled from —
                # provenance a reader can check, not a claim they must trust.
                "compiled_from": list(self._compiled_from),
                # What the terminal drains delivered (task t5). Rendered to
                # plain dicts, unlike ``degradations``: a host pipes this fold
                # to JSON, and the older key's dataclasses are already unpacked
                # by hand at every call site — a NEW key should not add a
                # second thing to remember before the report will serialise.
                "deliveries": [record.to_dict() for record in self._deliveries],
            }

    # ── the worker ───────────────────────────────────────────────────────────
    def _work(self) -> None:
        """The ONE thread's body: take a work item, think about it, repeat.

        Exits on the stop signal only. Everything inside
        :meth:`~embodiment.muse.MuseLoop.think` is already guaranteed not to
        raise, so the outer guard here is for this module's own bugs: a worker
        that dies must leave a record, not an absent mind the host mistakes for
        a quiet one.
        """
        try:
            while not self._stop.is_set():
                work = self._take()
                if work is None:
                    # Poll-wake: sleep until woken, or until the poll bound, and
                    # re-check the stop flag either way.
                    self._wake.wait(self._poll_interval)
                    self._wake.clear()
                    continue
                self._absorb(self._loop.think(work.boundary, recall_bundle=work.recall_bundle))
        except Exception as exc:  # a dead worker is recorded, never silent
            with self._lock:
                self._degrade(DEGRADED_WORKER, f"{type(exc).__name__}: {exc}")
        finally:
            self._idle.set()

    def _take(self) -> Optional[_Work]:
        """Claim the queued work item, or mark the lane idle. Worker thread only."""
        with self._lock:
            work = self._pending
            self._pending = None
            if work is None:
                self._idle.set()
                return None
            self._counts["sessions_started"] += 1
            self._work_started[work.work_class] = self._work_started.get(work.work_class, 0) + 1
            self._current_class = work.work_class
            return work

    def _deliver(self, insight: MuseInsight) -> None:
        """The muse's sink: buffer one insight as it is produced. Never raises.

        Totality matters here — a sink that raises is disabled by
        :mod:`embodiment.muse` for the rest of the session, which would silently
        halve a session's delivery. This one cannot raise, so incremental
        delivery is complete and ``MuseOutcome.insights`` is only ever used for
        accounting.
        """
        with self._lock:
            if self._closed or self._stop.is_set():
                self._drop_late(insight, "produced after the runner closed")
                return
            work_class = self._current_class
            if len(self._ready) == self._ready.maxlen:
                # The insight is lost to a full buffer either way, so the
                # overflow counter stays the honest total of what a full buffer
                # cost. The CODE splits on whether this was backpressure or a
                # priority inversion, because the two ask a host for different
                # things.
                self._counts["insights_dropped_overflow"] += 1
                evicted_class = self._ready[0][0]
                if work_class == WORK_COMPILATION and evicted_class == WORK_BOUNDARY:
                    self._counts["counsel_displaced"] += 1
                    self._record(
                        DROPPED_COUNSEL_DISPLACED,
                        f"drain buffer full ({self._ready.maxlen}); background "
                        "compilation displaced undrained boundary counsel, which "
                        "outranks it",
                        step_index=insight.origin.step_count,
                        model_turns=insight.turn_index,
                    )
                else:
                    self._record(
                        DROPPED_OVERFLOW,
                        f"drain buffer full ({self._ready.maxlen}); the oldest insight was "
                        "discarded so the freshest thinking survives",
                        step_index=insight.origin.step_count,
                        model_turns=insight.turn_index,
                    )
            self._ready.append((work_class, insight))

    def _absorb(self, outcome: MuseOutcome) -> None:
        """Fold one finished session's cost and degradations. Worker thread only."""
        with self._lock:
            self._counts["sessions_completed"] += 1
            # What the session spent on tools, if it had any. Read defensively
            # like every other host-facing value on this thread: a telemetry
            # field must not be the thing that kills the worker.
            self._counts["tool_rounds"] += _coerce_int(_read(outcome, "tool_rounds", 0))
            # Provenance: the ids this session's compiled memory cited. Held so
            # the host can carry them into the durable record's ``links`` — the
            # loop's memory boundary reads them back through
            # :attr:`compiled_from`.
            for record_id in outcome.compiled_from or ():
                self._compiled_from[str(record_id)] = None
            # The muse half of the relative-latency measurement. Only present
            # when the host injected a clock — absent, this stays empty and
            # `relative_latency` reports None rather than inventing a number.
            for insight in outcome.insights:
                if insight.latency is not None:
                    self._muse_turn_times.append(float(insight.latency))
            for degradation in outcome.degradations:
                self._record(
                    degradation.code,
                    degradation.reason,
                    step_index=degradation.step_index,
                    model_turns=degradation.model_turns,
                )
            if outcome.exit_reason == MUSE_EXIT_DEGRADED and not outcome.insights:
                self._failures += 1
                if self._failures >= self._max_failed:
                    self._degrade(
                        DEGRADED_ENDPOINT,
                        f"{self._failures} consecutive muse thinking sessions produced "
                        "nothing; the lane stops rather than re-dialling every boundary",
                    )
            else:
                self._failures = 0

    # ── recording (C3) ───────────────────────────────────────────────────────
    def _degrade(self, code: str, reason: str) -> None:
        """Stop the lane for good, recording why. Call with the lock held."""
        self._record(code, reason)
        if self._degradation is None:
            self._degradation = f"{code}: {reason}"[:_MAX_REASON_LEN]
        self._pending = None
        self._stop.set()
        self._wake.set()
        self._idle.set()

    def _drop_late(self, insight: MuseInsight, why: str) -> None:
        """Record one insight that has nowhere left to go. Call with the lock held."""
        self._counts["insights_dropped_late"] += 1
        self._record(
            DROPPED_LATE,
            why,
            step_index=insight.origin.step_count,
            model_turns=insight.turn_index,
        )

    def _record(self, code: str, reason: str, *, step_index: int = 0, model_turns: int = 0) -> None:
        """Append one transition. The counter is exact; the ledger keeps the tail.

        The ledger is a bounded window on the MOST RECENT transitions, because a
        long run under d1 legitimately produces many (a muse that runs behind
        supersedes boundaries and goes stale as a matter of course), and the
        recent ones are what diagnose a lane. Nothing important can be crowded
        out of it: the lifetime counters are exact and the lane's terminal
        degradation lives in its own field (:meth:`degradation`), not in here.

        Reuses :class:`~embodiment.muse.MuseDegradation` rather than minting a
        third degradation shape, so a host's ledger folds ONE record type from
        the loop, the muse and the runner alike.
        """
        self._counts["degradations_recorded"] += 1
        self._ledger.append(
            MuseDegradation(
                code=code,
                reason=str(reason)[:_MAX_REASON_LEN],
                step_index=_coerce_int(step_index),
                model_turns=_coerce_int(model_turns),
            )
        )

    def note_loop_step(self, seconds: Any) -> None:
        """Tell the runner how long one acting-loop step took.

        The loop half of the relative-latency measurement. The runner cannot
        observe this itself — it has no view of the acting loop — so a host that
        wants :meth:`snapshot`'s ``relative_latency`` populated feeds its own
        measured step durations here. A host that does not call this gets
        ``None``, which is the honest answer rather than a default standing in
        for a measurement nobody made.

        Never raises: an unreadable or non-positive value is ignored, because a
        telemetry call must not be able to break a drive.
        """
        try:
            value = float(seconds)
        except (TypeError, ValueError):
            return
        # NaN is rejected EXPLICITLY rather than as a side effect of comparison
        # order. Two earlier spellings were both worse: `value != value` reads
        # as a typo (and S1764 flags it as one), while `not value > 0` folds the
        # NaN case into an inverted comparison that S1940 then asks you to
        # "simplify" to `value <= 0` — which would silently let NaN through,
        # because every comparison against NaN is False, and one NaN poisons
        # every later mean in `_relative_latency`. Saying `isnan` out loud costs
        # one stdlib import and cannot be misread in either direction.
        if math.isnan(value) or value <= 0:
            return
        with self._lock:
            self._loop_step_times.append(value)

    def _relative_latency(self) -> Optional[float]:
        """Compute muse-to-loop relative latency from real measurements.

        Returns the ratio of mean muse turn time to mean loop step time, or
        ``None`` when there is no data. A value < 1.0 means the muse is faster
        (the live rig measured ~0.28, i.e. the muse is ~3.5× faster).

        The old ``DEFAULT_STALE_LAG = 5`` was chosen under the assumption the
        muse was slower and its insights arrived late. That assumption was
        measured false: the advisory lane is ~3.5× faster and ~30× cheaper per
        answer than the acting loop — 2.6s/50 tokens vs 9.3s/1479 tokens. The
        muse finishes first and waits.
        """
        if not self._muse_turn_times:
            return None
        muse_mean = sum(self._muse_turn_times) / len(self._muse_turn_times)
        if muse_mean == 0:
            return None
        if not self._loop_step_times:
            return None
        loop_mean = sum(self._loop_step_times) / len(self._loop_step_times)
        if loop_mean == 0:
            return None
        return muse_mean / loop_mean
