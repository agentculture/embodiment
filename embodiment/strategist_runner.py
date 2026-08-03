"""The strategist's THREAD — one daemon pump beside the acting loop (task t2).

:mod:`embodiment.scope` (task t1) is the strategist's *reasoning*: pure shapes,
directive validation and a bounded review loop with no thread, no clock and no
timer, deterministic to the last branch. This module is the other half, and only
the other half — the **thread mechanics** that let that loop run beside the
acting loop instead of inside it. Nothing here reasons; nothing there concurs.

CITED, not imported
-------------------
The mechanics below are ``embodiment/muse_runner.py``'s, copied into this file
and then **owned outright**. That module is roughly fourteen hundred lines of
concurrency correctness that took live probes to settle — the single replaceable
pending slot, the poll-wake read, the bounded join that never hangs on a parked
blocking read — and reimplementing it from a description would have risked
losing an invariant nobody remembered to write down.

So this lane imports nothing from :mod:`embodiment.muse` or
``embodiment.muse_runner`` (an AST test in ``tests/test_strategist_runner.py``
pins it), and because nothing depends on this copy, every name in it was free to
change. The muse is being archived (deviation ``d2``/``d3``, embodiment#53); it
stays readable as the citation. This module replaces it rather than extending
it, and the two deltas below are the whole reason it is a separate file.

Delta 1 — the output is AUTHORITY-BEARING
------------------------------------------
A muse insight that never arrives is lost counsel: the actor proceeds on its own
judgement, which is what it was going to do anyway. A strategic review that never
arrives is a lost **authority event** — a directive that would have changed what
the system was trying to do, or a hold that would have recorded a real
non-intervention. Constraint C3 already says a degradation must be observable;
here the bar is higher, because the thing lost had standing.

Every way a result can fail to reach the actor therefore has its own code, its
own exact counter, and a place in one of two accounting identities that
``tests/test_strategist_runner.py`` closes arithmetically:

* the **intake** identity — every snapshot offered is either reviewed, displaced
  from the pending slot, skipped by cadence, dropped late (offered to a closed or
  dead lane, or queued when the lane closed), or still queued right now;
* the **outcome** identity — every review completed is either delivered,
  dropped stale, dropped late, overflowed out of the buffer, superseded by a
  newer directive, or still buffered right now.

A number that does not close is a silent loss, and the test says which.

Delta 2 — the policy is DESIGNED, not inherited
------------------------------------------------
``muse.py``'s ``DEFAULT_STALE_LAG = 5`` was chosen expecting a big background
mind to *lag* the actor. That was measured false on 2026-07-25: the muse ran
~3.5x *faster* than the cortex, and the constant was never re-derived. The
strategist inverts the profile a third time — it is a dense 27B thinking model
that spends ~1000 reasoning tokens before it produces anything (measured
2026-07-30; visible in ``docs/live-test-results/arena-budget.md``'s truncated
cortex record, 4157 reasoning chars against zero content chars).

So no number was carried across. Every default below is derived from the
committed rate config
(``docs/live-test-results/timeout-rate-measurements.json``) in a comment a test
reads back out of this source, using three quantities:

``T_review_min``
    One review turn at the *fastest* measured cortex rate:
    ``(1000 reasoning + 300 directive) tokens / 25.4 tok/s`` = **51 s**.
``T_review_max``
    A full review at the *slowest* measured cortex rate, plus the committed
    non-generation allowance for queue wait and prefill:
    ``3 turns x 1300 tokens / 21.5 tok/s + 179.3 s`` = **361 s**. Three turns
    because :class:`~embodiment.scope.ScopeControls.max_turns` defaults to 3.
``T_actor_step``
    One acting step on the worker seat at its mean measured rate:
    ``1200 tokens / 38.9 tok/s`` = **31 s**.

Those are ratios of committed measurements, not preferences, and when the seats
are re-measured (task t10) the arithmetic re-runs rather than the opinion.

The lane instruments ITSELF, for the same reason
-------------------------------------------------
The muse constant went stale because nothing in the lane measured the quantity
it encoded. :meth:`StrategistRunner.note_actor_step` and
:meth:`StrategistRunner.state`'s ``relative_latency``, ``max_observed_lag`` and
``mean_observed_lag`` exist so a live run hands task t10 the real numbers instead
of the derivation above. A host that measures nothing gets ``None``, which is the
honest answer rather than a default standing in for a measurement nobody took.

The thread discipline, inherited from ``colleague/realtime.py``
---------------------------------------------------------------
* **ONE daemon thread.** No pool, no second pump, no thread per review.
* A :class:`threading.Event` **stop signal**, plus a wake event so teardown is
  prompt rather than parked on a poll interval.
* A **poll-wake wait**: the worker wakes on the event *or* on a bounded poll
  interval, and re-checks the stop flag either way, so a missed wakeup costs
  latency and never a hang.
* :func:`_bounded_join` — teardown **never hangs**, even when the strategist is
  parked inside a model call nothing can interrupt. The thread is a daemon
  precisely so an unreapable review cannot keep a host's process alive.
* **Degrade, never raise.** A start failure, a dead seam, a mid-run failure and
  a worker that dies each flip a degraded flag and record a transition. No
  exception ever crosses back into the acting loop's main path.

What was deliberately NOT copied
---------------------------------
Three of the muse runner's surfaces have no counterpart here, and their absence
is a decision rather than an omission:

* **The second work class.** The muse had ``boundary`` and ``compilation``
  contending for one thread. The strategist has one kind of work — a review —
  so the scheduling policy that ranked them is not needed and its two codes are
  not declared.
* **The delivery ledger.** The cited runner minted a delivery record at its
  terminal drain because a specific published claim rested on that beat. No
  claim here rests on it; exact counters carry the same information without a
  second stream, and task t5 owns observability proper through the host-wired
  observer seam.
* **The snapshot copy.** ``_carry`` copied a boundary's live ``history`` list
  before it crossed onto the worker thread.
  :class:`~embodiment.scope.ScopeSnapshot` already tuple-ises every sequence and
  copies ``resource_state`` in ``__post_init__``, so the crossing is immutable by
  construction and a second copy would be ceremony.

The register is what was ISSUED; the drain is what was RECEIVED
----------------------------------------------------------------
Read this before wiring the composition layer (task t4), because the two are
deliberately not the same set and a reader who assumes they are will be wrong in
one specific way.

:meth:`~embodiment.scope.ScopeLoop.review` offers a directive to the
:class:`~embodiment.scope.ScopeRegister` **inside** the review, on the worker
thread, before this module ever sees the outcome. That is where validation and
version ordering live and it is right that they do: the chain has to be
consistent even for a directive nobody ends up applying, or two reviews could
mint the same version.

So a directive this runner then drops as stale (:data:`DROPPED_STALE`) or
overtaken (:data:`DROPPED_SUPERSEDED`) is **already in the chain**, and
:attr:`~StrategistRunner.active_directive` can name a directive the actor never
received. That is not a leak: the register answers *"what has the strategist
issued, and in what order?"*, while :meth:`~StrategistRunner.drain` answers
*"what reached the acting loop?"*. The drop record is the join between them, and
it is why every one of them is counted.

What follows for a host: **apply what ``drain`` hands you, never what the
register holds.** Reading scope off the register would apply a directive this
lane deliberately withheld — which is precisely the silent restore of superseded
scope the composition layer must not perform.

One close, for things this module knows nothing about
-----------------------------------------------------
A host that hands the strategist a bench has a second lifetime to end, and it
has to end after the review thread stops, not before. :data:`Closer` is that
seam: ``StrategistRunner(complete, closers=(store.close,))`` runs an opaque
zero-argument callable once at :meth:`StrategistRunner.close`, after the bounded
join. The runner never learns what it closed, imports nothing to support it, and
a failure is recorded (:data:`DEGRADED_CLOSER`) rather than raised.

Observability is PULL-only
--------------------------
:meth:`StrategistRunner.state`, :attr:`~StrategistRunner.degradations` and
:attr:`~StrategistRunner.counts` are folds of what already happened. There is
deliberately no ``on_degradation`` callback and no listener registry: firing host
code from this thread is exactly how a "never blocks the actor" promise gets
quietly broken, and the scope event stream is the host-wired observer's job
(task t5), not a back door here.

Scope authority only — structurally
------------------------------------
This module drives a :class:`~embodiment.scope.ScopeLoop` and hands back
:class:`~embodiment.scope.ScopeOutcome` values, which carry a directive and
provenance and nothing else. It builds no executor, constructs no tool and holds
no decision vocabulary in scope: it never imports :mod:`embodiment.loop`. There
is no path from anything here to a tool-call decision, and that is held by the
mechanism rather than by promise (`colleague#352
<https://github.com/agentculture/colleague/issues/352>`_).

For the ledger lane (task t3)
------------------------------
:data:`LANE_CODES` is what a record on this lane can carry: the ten codes this
module MINTS (:data:`RUNNER_CODES`) plus the eleven :mod:`embodiment.scope`
codes it RELAYS, because :meth:`StrategistRunner._absorb` copies a finished
review's own degradations onto this ledger exactly as the muse runner copied the
muse's. All twenty-one are exported through ``__all__`` so a single
``_MODULES`` row pointed at this module harvests the whole scope vocabulary —
which is what task t3's "exactly one row" acceptance needs.

Stdlib only beyond :mod:`embodiment.scope` (constraint C1): ``collections``,
``dataclasses``, ``math``, ``threading``, ``typing``.
"""

from __future__ import annotations

import math
import threading
from collections import deque
from dataclasses import asdict, dataclass
from typing import Any, Callable, Iterable, Optional

from embodiment.scope import (
    DEGRADED_MALFORMED,
    DEGRADED_REVIEW,
    DEGRADED_TOOL,
    DEGRADED_TOOL_ROUNDS,
    DEGRADED_TRUNCATED,
    DEGRADED_UNREADABLE,
    DROPPED_AUTHORITY,
    DROPPED_DUPLICATE,
    DROPPED_INCOMPLETE,
    DROPPED_UNKNOWN_SUPERSEDES,
    DROPPED_VERSION_BACKWARD,
    SCOPE_EXIT_DEGRADED,
    SCOPE_EXIT_DIRECTIVE,
    SCOPE_EXIT_UNCHANGED,
    ScopeCompleteFn,
    ScopeControls,
    ScopeDegradation,
    ScopeDirective,
    ScopeLoop,
    ScopeOutcome,
    ScopeRegister,
    ScopeSnapshot,
    ScopeToolBench,
)

__all__ = [
    # role + thread identity
    "STRATEGIST_ROLE",
    "THREAD_NAME",
    # the transition vocabulary this lane MINTS (C3)
    "DEGRADED_THREAD",
    "DEGRADED_WORKER",
    "DEGRADED_SEAM",
    "DEGRADED_CLOSER",
    "DROPPED_STALE",
    "DROPPED_LATE",
    "DROPPED_OVERFLOW",
    "DROPPED_SNAPSHOT",
    "DROPPED_SUPERSEDED",
    "DROPPED_CADENCE",
    "RUNNER_CODES",
    # the review-level vocabulary this lane RELAYS through ``_absorb``
    "DEGRADED_REVIEW",
    "DEGRADED_UNREADABLE",
    "DEGRADED_TRUNCATED",
    "DEGRADED_MALFORMED",
    "DEGRADED_TOOL",
    "DEGRADED_TOOL_ROUNDS",
    "DROPPED_INCOMPLETE",
    "DROPPED_DUPLICATE",
    "DROPPED_UNKNOWN_SUPERSEDES",
    "DROPPED_VERSION_BACKWARD",
    "DROPPED_AUTHORITY",
    "LANE_CODES",
    # defaults, each derived in a comment above it
    "DEFAULT_MAX_PENDING",
    "DEFAULT_MAX_LAG",
    "DEFAULT_MAX_FAILED_REVIEWS",
    "DEFAULT_JOIN_TIMEOUT",
    "DEFAULT_POLL_INTERVAL",
    "DEFAULT_REVIEW_GAP",
    "MAX_LEDGER",
    # the runner
    "StrategistLimits",
    "ThreadFactory",
    "Closer",
    "StrategistRunner",
]


# ── identity ──────────────────────────────────────────────────────────────────

#: The ROLE this runner drives, by name. Roles resolve by name from a host's own
#: capability contract — never by parsing a model name (there is no model name
#: anywhere in this module, on purpose). The reference rig seats this on the
#: gateway's ``cortex`` role, but that mapping is the HOST's configuration and
#: this label is only what the record says.
STRATEGIST_ROLE = "strategist"

#: The one thread's name. Fixed so a host's stack dump or profiler names it.
THREAD_NAME = "embodiment-strategist"


# ── the transition vocabulary this lane MINTS (C3) ────────────────────────────
#
# Every code is prefixed ``strategist-`` so the ledger harvest cannot collide
# with another lane's, and every one has a producer in this file plus a test
# that fires it (embodiment#18: a code nothing can mint is a lie in the ledger).

#: The worker thread could not be started; the lane never runs at all.
DEGRADED_THREAD = "strategist-thread-unavailable"
#: The worker itself died on an unexpected exception. It should be unreachable —
#: :meth:`embodiment.scope.ScopeLoop.review` never raises — so if it fires, the
#: record is the only thing standing between a bug and a silently absent mind.
DEGRADED_WORKER = "strategist-worker-failed"
#: Consecutive reviews failed outright; the lane stops dialling. Distinct from a
#: single :data:`DEGRADED_REVIEW`, which is one review's fault and is relayed.
DEGRADED_SEAM = "strategist-seam-dead"
#: A host-wired teardown callable raised at :meth:`StrategistRunner.close`. The
#: lane is already stopping, so this never propagates — but a teardown that did
#: not happen is exactly the kind of loss C3 refuses to leave unsaid.
DEGRADED_CLOSER = "strategist-closer-failed"
#: A finished review fell further behind the acting loop than ``max_lag`` steps.
#: The scope it reasoned about is gone, so applying its directive would govern
#: the system by a picture of the world that no longer exists.
DROPPED_STALE = "strategist-review-stale"
#: A review arrived (or was still buffered) after the runner closed, or a
#: snapshot was offered to a lane that had already closed or degraded. Either
#: way an authority event had nowhere to go.
DROPPED_LATE = "strategist-review-late"
#: The drain buffer was full; the oldest review was discarded to make room, so
#: the freshest strategic reading survives.
DROPPED_OVERFLOW = "strategist-review-overflow"
#: A queued snapshot was replaced by a newer one before it was ever reviewed.
#: The single pending slot's whole purpose, and its cost, recorded.
DROPPED_SNAPSHOT = "strategist-snapshot-displaced"
#: A buffered directive stopped being the head of the chain before the actor
#: drained it: a later review issued a higher version while it waited. Delivering
#: it would hand the host scope the register has already moved past.
DROPPED_SUPERSEDED = "strategist-directive-superseded"
#: A snapshot arrived inside the cadence gap and no review was started for it.
#: Deliberately a recorded drop rather than a silent no-op: skipping a review is
#: a decision not to exercise strategic authority at that boundary.
DROPPED_CADENCE = "strategist-review-skipped"

#: The complete set of codes this module MINTS. Ten, and it mints nothing else.
RUNNER_CODES = (
    DEGRADED_THREAD,
    DEGRADED_WORKER,
    DEGRADED_SEAM,
    DEGRADED_CLOSER,
    DROPPED_STALE,
    DROPPED_LATE,
    DROPPED_OVERFLOW,
    DROPPED_SNAPSHOT,
    DROPPED_SUPERSEDED,
    DROPPED_CADENCE,
)

#: Everything a record on this lane can carry: what this module mints, plus the
#: review-level vocabulary :meth:`StrategistRunner._absorb` copies verbatim from
#: a finished :class:`~embodiment.scope.ScopeOutcome`. Declared here so the
#: ledger's scope lane (task t3) reads ONE vocabulary from ONE module rather
#: than reconciling two.
LANE_CODES = RUNNER_CODES + (
    DEGRADED_REVIEW,
    DEGRADED_UNREADABLE,
    DEGRADED_TRUNCATED,
    DEGRADED_MALFORMED,
    DEGRADED_TOOL,
    DEGRADED_TOOL_ROUNDS,
    DROPPED_INCOMPLETE,
    DROPPED_DUPLICATE,
    DROPPED_UNKNOWN_SUPERSEDES,
    DROPPED_VERSION_BACKWARD,
    DROPPED_AUTHORITY,
)


# ── the derived defaults ──────────────────────────────────────────────────────
#
# Read the module docstring first: T_review_min = 51 s, T_review_max = 361 s and
# T_actor_step = 31 s are all ratios of figures committed in
# docs/live-test-results/timeout-rate-measurements.json. Nothing below is a
# number someone liked; each is that arithmetic, stated where it is used.

#: How many undrained reviews the buffer holds before discarding the oldest.
#: DERIVATION: the producer is strictly slower than the consumer here, which is
#: the opposite of the muse's situation. The lane emits at most one review per
#: T_review_min = 51 s (one turn at the fastest measured cortex rate, 25.4
#: tok/s), and the actor drains at every safe boundary, T_actor_step = 31 s
#: apart — so a depth of 2 already cannot be exceeded by a healthy lane. 4 is
#: 2x that, headroom for a host that drains only at coarse boundaries. The
#: muse's 32 would be dead capacity AND would delay the overflow record, which
#: is the part that matters: an overflow here means the actor stopped draining,
#: and a small buffer surfaces that in one review instead of thirty-two.
DEFAULT_MAX_PENDING = 4
#: How many acting steps a finished review may fall behind before :meth:`drain`
#: drops it as stale.
#: DERIVATION: ceil(T_review_max / T_actor_step) = ceil(361 s / 31 s) = 12.
#: T_review_max is a full three-turn review at the slowest measured cortex rate
#: (21.5 tok/s) plus the committed 179.3 s non-generation allowance for queue
#: wait and prefill; T_actor_step is one worker-seat turn at its mean measured
#: rate (38.9 tok/s). A threshold below that would discard the lane's own
#: nominal worst case on arrival and make the whole seam a no-op — which is the
#: failure the muse's inherited 5 would have produced here. Note also that scope
#: ages more slowly than counsel: an objective set at step 3 is often still the
#: right objective at step 40, so this threshold is here to catch a
#: pathologically late review, not a merely slow one. ``0`` disables it.
DEFAULT_MAX_LAG = 12
#: Consecutive failed reviews tolerated before the lane stops dialling.
#: DERIVATION: one failed review can occupy the seam for the whole request bound,
#: T_review_max = 361 s, so re-dialling a dead seam at every boundary costs
#: minutes of a drive rather than the seconds it cost a fast advisor. One
#: failure is therefore enough evidence to stop. A host that expects a flaky
#: link raises it deliberately.
DEFAULT_MAX_FAILED_REVIEWS = 1
#: Bound on :meth:`StrategistRunner.close`'s join. Teardown never hangs.
#: DERIVATION: 2 x DEFAULT_POLL_INTERVAL. No join bound could ever catch a review
#: in flight — the SHORTEST possible one is T_review_min = 51 s, twenty-five
#: times this — so this bound's only job is to reap a worker sitting in the
#: poll-wake wait between reviews. ``close`` sets the wake event, so that worker
#: exits immediately; two poll intervals covers a missed wakeup and nothing
#: more. A worker parked inside a model call is deliberately not waited for: it
#: is a daemon thread and cannot keep the host's process alive.
DEFAULT_JOIN_TIMEOUT = 2.0
#: The worker's poll-wake bound. Correctness never depends on it — the wake
#: event does the work — so it is a safety net for a missed wakeup, not a clock.
#: DERIVATION: 1.0 s is 2.0% of T_review_min = 51 s, the shortest review this
#: seat can produce. A missed wakeup therefore costs at most 2% of one work
#: unit, which is noise, while waking twice a second (the muse's interval, sized
#: against its ~2.6 s sessions) would be pure overhead against a work unit three
#: orders of magnitude longer.
DEFAULT_POLL_INTERVAL = 1.0
#: Minimum acting steps between review STARTS — the cadence gap.
#: DERIVATION: ceil(T_review_min / T_actor_step) = ceil(51 s / 31 s) = 2. The
#: lane never starts a second review before the fastest possible one could have
#: finished, so a deep thinker is not offered work it provably cannot have
#: absorbed yet. This matters in tokens as well as time: the muse's measured
#: intervention rate was 1.2% for 2.4-4.4x the token cost, and reviewing at
#: every boundary is how that arithmetic gets worse. A snapshot carrying an
#: explicit ``requested_decision`` always passes — an escalation is the actor
#: asking a question, not a cadence tick. ``0`` disables the gap.
DEFAULT_REVIEW_GAP = 2
#: How many of the most recent transitions the ledger keeps. The counters stay
#: exact past it, and the lane's terminal degradation lives in its own field.
#: DERIVATION: at most a handful of records per review, and a review costs at
#: least T_review_min = 51 s, so 100 records span upward of 85 minutes of a
#: healthy lane — longer than any drive this seat has run. The bound exists so a
#: pathological lane cannot grow it without limit, not to ration a normal one.
MAX_LEDGER = 100

#: Cap on one record's reason text, mirroring :mod:`embodiment.scope`.
_MAX_REASON_LEN = 500

#: Builds the worker thread. Injected so a test can assert a strategistless run
#: creates none, and so a host with its own thread policy can supply one.
ThreadFactory = Callable[..., Any]

#: One host-owned teardown, tied to this lane's close. A zero-argument callable
#: and **nothing more** — the runner never inspects it, never learns what it
#: closes, and imports nothing to accommodate it. Ownership stays where
#: construction is; only the *timing* is delegated here.
Closer = Callable[[], Any]


@dataclass(frozen=True)
class StrategistLimits:
    """The lane's six tuning scalars, in one default-constructed object.

    **Why this shape exists at all.** The cited runner's constructor grew to
    fifteen parameters and tripped ``python:S107``; the disposition recorded in
    ``docs/sonar-dispositions.md`` accepted it there because that class is
    published API with external consumers, and named this grouping as the future
    path. A brand-new module has no external consumers, so it takes that path on
    day one instead of inheriting the debt: with these six behind one argument,
    :meth:`StrategistRunner.__init__` lands at eleven parameters.

    Every field is read through :func:`_coerce_int` / :func:`_coerce_float`, so a
    host handing in a partially-built or hostile object degrades to the derived
    defaults rather than breaking a drive. The defaults themselves are derived
    above, each in a comment a test reads back out of the source.

    Fields
    ------
    max_pending:
        Undrained reviews held before the oldest is discarded (and recorded).
    max_lag:
        Acting steps a finished review may fall behind before it is dropped as
        stale. ``0`` disables staleness entirely.
    max_failed_reviews:
        Consecutive failed reviews tolerated before the lane stops dialling.
    join_timeout:
        The default bound on :meth:`StrategistRunner.close`'s join.
    poll_interval:
        The worker's poll-wake bound (a safety net, not a clock).
    review_gap:
        Minimum acting steps between review starts. ``0`` disables cadence.
    """

    max_pending: int = DEFAULT_MAX_PENDING
    max_lag: int = DEFAULT_MAX_LAG
    max_failed_reviews: int = DEFAULT_MAX_FAILED_REVIEWS
    join_timeout: float = DEFAULT_JOIN_TIMEOUT
    poll_interval: float = DEFAULT_POLL_INTERVAL
    review_gap: int = DEFAULT_REVIEW_GAP


# ── never-raising helpers ─────────────────────────────────────────────────────


def _bounded_join(thread: Any, *, timeout: float = DEFAULT_JOIN_TIMEOUT) -> None:
    """Join *thread* with a bounded timeout — never hangs.

    The discipline is ``colleague/realtime.py``'s, inherited rather than
    imported: every pump thread is a daemon stopped through a
    :class:`threading.Event` and reaped through a bounded join, so a review
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
    except Exception:  # noqa: BLE001  # a junk count is a default, never a crash
        return default


def _coerce_float(value: Any, default: float) -> float:
    """Best-effort ``float``; anything uncoercible or non-finite is *default*."""
    try:
        number = float(value)
    except Exception:  # noqa: BLE001  # a junk interval is a default, never a crash
        return default
    if math.isnan(number) or number <= 0:
        return default
    return number


def _read(obj: Any, name: str, default: Any = None) -> Any:
    """``getattr`` that cannot raise — a hostile property reads as absent.

    Every snapshot field this module touches goes through here, because
    :meth:`StrategistRunner.consider` runs on the ACTOR's thread: an exception
    raised reading a host's object would be an exception in the acting loop's
    main path, which is the one thing a presence layer may never do. The review
    itself reads the snapshot again, defensively, one layer down in
    :mod:`embodiment.scope`.
    """
    try:
        return getattr(obj, name, default)
    except Exception:  # noqa: BLE001  # an unreadable attribute is simply absent
        return default


def _label(snapshot: Any) -> str:
    """A snapshot's id, for a record's reason text. Never raises."""
    value = _read(snapshot, "snapshot_id", "")
    return value if isinstance(value, str) else ""


@dataclass(frozen=True)
class _Work:
    """One unit of strategist work: what to review, and where the actor was.

    Private on purpose. A host offers work through
    :meth:`StrategistRunner.consider` and the runner stamps the step; there is no
    shape here for a host to construct. Frozen so a queued item cannot be edited
    after the cadence and displacement decisions that admitted it.
    """

    snapshot: Any
    step_index: int = 0


class StrategistRunner:
    """Run ONE :class:`~embodiment.scope.ScopeLoop` on one daemon thread.

    ``consider`` offers a snapshot and returns; ``drain`` collects whatever
    finished; ``degradation`` reports the lane having stopped. None of the three
    blocks, and none of them raises — including against a seam that never
    returns at all.

    Args:
        complete: the injected strategist seam
            (:data:`~embodiment.scope.ScopeCompleteFn`), already carrying its own
            endpoint. This module never infers a model, a role or an address.
        role: the role NAME this lane drives, recorded for provenance. It names
            who is reviewing, never what may be inferred from it.
        model: the model id the seam is configured to call, for the record.
            Host-declared and empty by default — which is what keeps a
            single-model run from claiming a strategist exists (colleague#352).
        controls: the review loop's turn budget and caps.
        register: the directive chain. A host supplies one to seat its explicit
            default scope, or to hold a persistence lane of its own; omitted, the
            loop keeps a fresh one that survives across reviews. **The runner
            owns it for the lane's lifetime** — it is written on the worker
            thread, so a host that keeps mutating its own copy is racing itself.
        system: OPTIONAL host framing, appended to the strategist's authority
            boundary (never substituted for it).
        clock: the ONLY source of a latency measurement, handed to the review
            loop. Absent, every ``latency`` stays ``None`` rather than a
            fabricated zero — and :meth:`state`'s ``relative_latency`` stays
            ``None`` with it.
        tools: OPTIONAL :class:`~embodiment.scope.ScopeToolBench`, handed
            straight to the loop and never kept here. ``None`` — the default —
            is the tools-off lane, and on that path the constructed loop and
            every prompt it sends are what they were before this argument
            existed.
        limits: the six tuning scalars (:class:`StrategistLimits`). Omitted, the
            derived defaults apply; a hostile object degrades to them field by
            field.
        thread_factory: builds the worker thread; :class:`threading.Thread` by
            default. Called with ``target``, ``name`` and ``daemon`` keywords.
        closers: host-owned teardowns to run once at :meth:`close`, in order,
            after the bounded join and after the late-drop accounting. Each must
            **bound itself**; the runner deliberately does not wrap them in a
            second bound, because an outer timeout would cut off an inner one
            before it could record what it left behind.

    Lifecycle::

        with StrategistRunner(complete) as strategist:   # no thread yet
            strategist.consider(projector(), step_index=step)
            for outcome in strategist.drain(step_count=step):
                apply_at_next_safe_boundary(outcome)
        # closed: stop signalled, join bounded, undrained reviews recorded

    The runner must not outlive the drive: :meth:`close` is idempotent, bounded,
    and safe to call from anywhere including the worker thread itself (the join
    skips the current thread rather than deadlocking).
    """

    def __init__(
        self,
        complete: ScopeCompleteFn,
        *,
        role: str = STRATEGIST_ROLE,
        model: str = "",
        controls: Optional[ScopeControls] = None,
        register: Optional[ScopeRegister] = None,
        system: Optional[str] = None,
        clock: Optional[Callable[[], float]] = None,
        tools: Optional[ScopeToolBench] = None,
        limits: Optional[StrategistLimits] = None,
        thread_factory: Optional[ThreadFactory] = None,
        closers: Iterable[Closer] = (),
    ) -> None:
        self._role = str(role or STRATEGIST_ROLE)
        self._model = str(model or "")
        # Materialised at construction, not at close: a generator handed here
        # would be consumed by an earlier close and silently empty at the one
        # that matters. Deliberately NOT wrapped in a try — a ``closers`` that is
        # not iterable is a wiring mistake, and failing at construction beats
        # discovering at teardown that nothing was ever going to be closed.
        self._closers: tuple[Closer, ...] = tuple(closers)
        # ONE loop instance, driven by ONE thread, one review at a time — the
        # protocol embodiment.scope documents for exactly this consumer. The
        # bench is handed over and NOT stored: there is no attribute here holding
        # a host's bench, so nothing in this module — ``close`` included — can
        # reach one to inspect it, call it or tear it down.
        self._loop = ScopeLoop(
            complete,
            controls=controls,
            register=register,
            system=system,
            clock=clock,
            tools=tools,
            model=self._model,
            role=self._role,
        )
        self._limits = _resolve_limits(limits)
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
        self._ready: deque[ScopeOutcome] = deque(maxlen=self._limits.max_pending)
        self._ledger: deque[ScopeDegradation] = deque(maxlen=MAX_LEDGER)
        self._failures = 0
        self._observed_step = 0
        #: The acting step the most recently STARTED review was about. Written by
        #: ``_take`` on the worker thread, read by ``_cadence_blocks`` on the
        #: actor's — both under the lock.
        self._last_review_step = 0
        #: The highest directive version any completed review has produced. Held
        #: here rather than read back off the register from another thread, so
        #: supersession is judged under this module's own lock.
        self._latest_version = 0
        self._counts = {
            # intake — what happened to snapshots offered to the lane
            "snapshots_offered": 0,
            "snapshots_displaced": 0,
            "snapshots_skipped_cadence": 0,
            "snapshots_dropped_late": 0,
            "reviews_started": 0,
            # outcomes — what happened to reviews the lane completed
            "reviews_completed": 0,
            "reviews_delivered": 0,
            "reviews_dropped_stale": 0,
            "reviews_dropped_late": 0,
            "reviews_dropped_overflow": 0,
            "directives_superseded": 0,
            # what the delivered reviews actually said. A hold is a REAL answer
            # and is counted as one: non-intervention is a graded outcome, and a
            # lane that only counted directives would report a careful
            # strategist as an idle one.
            "directives_delivered": 0,
            "holds_delivered": 0,
            # cost and faults
            "tool_rounds": 0,
            "degradations_recorded": 0,
        }
        # Relative latency: review times vs acting step times. The review side
        # fills itself from each finished review (see :meth:`_absorb`); the actor
        # side can only come from the host, which is the only party that knows
        # how long its own steps took (see :meth:`note_actor_step`).
        self._review_times: list[float] = []
        self._actor_step_times: list[float] = []
        #: Every delivered review's actual step lag. The direct empirical input
        #: for re-deriving ``max_lag`` (task t10) — the measurement whose absence
        #: is why the muse's constant went stale unnoticed.
        self._observed_lags: list[int] = []

    # ── the drain-shaped seam ────────────────────────────────────────────────
    def consider(self, snapshot: Optional[ScopeSnapshot], *, step_index: int = 0) -> None:
        """Offer one snapshot to review, then return. Never blocks or raises.

        At most one snapshot is ever queued: a snapshot arriving while a review
        is in flight REPLACES any snapshot still waiting, so the strategist
        always reviews the most recent projection rather than working through a
        backlog of stale ones. The displaced snapshot is recorded
        (:data:`DROPPED_SNAPSHOT`), never silently dropped — it was an
        opportunity to exercise strategic authority, and it was spent.

        *step_index* is the acting loop's current step. It rides the review and
        every record on it, and it is what both the cadence gap and the staleness
        threshold measure distance in.
        """
        if snapshot is None:
            return
        self._offer(_Work(snapshot, _coerce_int(step_index)))

    def _offer(self, work: _Work) -> None:
        """Queue one review under the cadence and displacement policy. Never raises."""
        with self._lock:
            self._counts["snapshots_offered"] += 1
            if self._closed or self._degradation is not None:
                self._drop_late_snapshot(work, "the lane had already closed or degraded")
                return
            if self._cadence_blocks(work):
                return
            if self._pending is not None:
                self._counts["snapshots_displaced"] += 1
                self._record(
                    DROPPED_SNAPSHOT,
                    f"snapshot {_label(self._pending.snapshot)!r} was superseded by a newer "
                    "projection before any review of it began",
                    step_index=self._pending.step_index,
                )
            self._pending = work
            self._observed_step = max(self._observed_step, work.step_index)
            self._idle.clear()
        if not self.start():
            # No thread means nothing will ever pick this up; say so rather than
            # leaving an authority event queued against a lane that does not
            # exist. Recorded against ``work`` rather than against whatever is
            # in the slot: a failing ``start`` degrades the lane, and
            # :meth:`_degrade` has already emptied the slot by the time this
            # runs, so reading it back would record nothing at all.
            with self._lock:
                self._pending = None
                self._idle.set()
                self._drop_late_snapshot(work, "no worker thread could be started")
            return
        self._wake.set()

    def _cadence_blocks(self, work: _Work) -> bool:
        """Whether the cadence gap refuses *work*. Call with the lock held.

        Four ways through, and each is a decision rather than a special case:

        * ``review_gap <= 0`` — a host that turned cadence off;
        * no review has started yet — the first one is never gated;
        * the snapshot carries a ``requested_decision`` — an explicit escalation
          from the actor is a question, not a cadence tick, and a governor that
          declined to answer it would be the failure this lane exists to prevent;
        * ``step_index <= 0`` — cadence is a step DISTANCE, and a host that
          supplies no steps has none to measure. Symmetric with ``max_lag``,
          which is inert on the same input for the same reason.
        """
        gap = self._limits.review_gap
        if gap <= 0 or self._counts["reviews_started"] == 0 or work.step_index <= 0:
            return False
        if _read(work.snapshot, "requested_decision"):
            return False
        if (work.step_index - self._last_review_step) >= gap:
            return False
        self._counts["snapshots_skipped_cadence"] += 1
        self._record(
            DROPPED_CADENCE,
            f"snapshot {_label(work.snapshot)!r} arrived {work.step_index - self._last_review_step}"
            f" step(s) after the last review started; the cadence gap is {gap}",
            step_index=work.step_index,
        )
        return True

    def drain(self, *, step_count: int = 0) -> list[ScopeOutcome]:
        """Return whatever reviews are ready NOW. Never blocks or raises.

        An empty list is the normal case — the strategist reviews on its own
        clock and an acting loop must never wait on it. *step_count* is the
        actor's current step, and it decides two things:

        * **staleness** — a review more than ``max_lag`` steps behind is dropped
          and recorded (:data:`DROPPED_STALE`);
        * nothing else. Supersession is judged against the directive chain, not
          against the clock.

        A buffered directive that a later review has already overtaken is dropped
        as :data:`DROPPED_SUPERSEDED` rather than handed over, because delivering
        it would offer the host scope the register has moved past — the "no
        silent restore of superseded scope" rule, held here so the composition
        layer does not have to re-derive it.
        """
        with self._lock:
            self._observed_step = max(self._observed_step, _coerce_int(step_count))
            current = self._observed_step
            ready = list(self._ready)
            self._ready.clear()
            kept = [outcome for outcome in ready if self._keep(outcome, current)]
            self._counts["reviews_delivered"] += len(kept)
            return kept

    def _keep(self, outcome: ScopeOutcome, current: int) -> bool:
        """Whether one buffered review reaches the actor. Call with the lock held."""
        lag = current - _coerce_int(_read(outcome, "step_index", 0))
        max_lag = self._limits.max_lag
        if max_lag > 0 and lag > max_lag:
            self._counts["reviews_dropped_stale"] += 1
            self._record(
                DROPPED_STALE,
                f"a review of step {outcome.step_index} was read at step {current} "
                f"(lag {lag} > {max_lag}); the scope it reasoned about is gone",
                step_index=outcome.step_index,
                model_turns=outcome.turns,
            )
            return False
        directive = _read(outcome, "directive")
        if (
            directive is not None
            and _coerce_int(_read(directive, "version")) < self._latest_version
        ):
            self._counts["directives_superseded"] += 1
            self._record(
                DROPPED_SUPERSEDED,
                f"directive {_read(directive, 'scope_id')!r} (version "
                f"{_read(directive, 'version')}) was overtaken by version "
                f"{self._latest_version} before the actor drained it",
                step_index=outcome.step_index,
                model_turns=outcome.turns,
            )
            return False
        self._observed_lags.append(lag)
        if outcome.exit_reason == SCOPE_EXIT_DIRECTIVE:
            self._counts["directives_delivered"] += 1
        elif outcome.exit_reason == SCOPE_EXIT_UNCHANGED:
            self._counts["holds_delivered"] += 1
        return True

    def degradation(self) -> Optional[str]:
        """Why this lane stopped reviewing, or ``None`` while it is healthy."""
        with self._lock:
            return self._degradation

    # ── lifecycle ────────────────────────────────────────────────────────────
    def start(self) -> bool:
        """Start the worker thread if it is not running. Idempotent; never raises.

        Returns ``True`` when a live thread exists afterwards. A thread that
        cannot be started degrades the lane permanently, visibly and quietly —
        the host's drive continues actor-only, which is the default supported
        path anyway.
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

        Idempotent and safe to call from any thread, the worker's included.
        Reviews still buffered — and any produced after this returns — have
        nowhere to go, so each is recorded as a late drop (C3) rather than
        vanishing; so is a snapshot that was queued and never reviewed.

        **The order is load-bearing.** Closers run *last*, after the bounded
        join, because a closer's subject may still be in use by a review the join
        is waiting on. They also run after the late-drop accounting, so a closer
        reading :attr:`counts` sees a finished lane rather than one mid-close.

        **What "never hangs" covers.** The join is bounded, and a review parked
        inside an uninterruptible model call is simply left to the daemon thread
        — that part is unconditional. A ``closers`` entry is the host's own code
        on the host's own budget: the runner runs each exactly once and lets none
        of them raise, but it does not bound them.
        """
        with self._lock:
            first = not self._closed
            self._closed = True
            thread = self._thread
            stranded = list(self._ready)
            self._ready.clear()
            queued = self._pending
            self._pending = None
            if first and queued is not None:
                self._drop_late_snapshot(queued, "the lane closed before its review began")
        self._stop.set()
        self._wake.set()
        _bounded_join(thread, timeout=self._limits.join_timeout if timeout is None else timeout)
        with self._lock:
            self._idle.set()
            if first:
                for outcome in stranded:
                    self._drop_late_review(outcome, "undrained when the runner closed")
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
        """Block until no review is in flight and none is queued.

        A bounded wait on a real condition, for orderly shutdown and for tests —
        which is why the concurrency tests need no sleeps. **The acting loop must
        never call this on its hot path**: waiting on the strategist is the one
        thing this lane exists to avoid. Returns ``False`` on timeout.
        """
        return self._idle.wait(timeout)

    def __enter__(self) -> "StrategistRunner":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    # ── pull-only observability ──────────────────────────────────────────────
    @property
    def role(self) -> str:
        """The role NAME this lane drives — provenance, never a capability."""
        return self._role

    @property
    def model(self) -> str:
        """The host-declared model id, or ``""`` when none was declared."""
        return self._model

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def thread_started(self) -> bool:
        """Whether a worker thread was ever created. A strategistless run: never."""
        return self._thread is not None

    @property
    def register(self) -> ScopeRegister:
        """The directive chain this lane admits into — what was ISSUED.

        Read-only by convention, and **not** the set of directives the actor
        received: see the module docstring's "The register is what was ISSUED"
        section. A host applies :meth:`drain`'s output, never this.
        """
        return self._loop.register

    @property
    def active_directive(self) -> Optional[ScopeDirective]:
        """The head of the issued chain, or ``None`` before anything was accepted.

        It may name a directive :meth:`drain` withheld as stale or superseded.
        Useful for a host reporting what the strategist decided; wrong as the
        source of what the actor should be working under.
        """
        return self._loop.register.active

    @property
    def degradations(self) -> list[ScopeDegradation]:
        """The most recent transitions, at most :data:`MAX_LEDGER` of them."""
        with self._lock:
            return list(self._ledger)

    @property
    def counts(self) -> dict[str, int]:
        """Exact counters — never truncated, even when the ledger is."""
        with self._lock:
            return dict(self._counts)

    def state(self) -> dict[str, Any]:
        """A PULL-ONLY fold of what already happened. Returns copies."""
        with self._lock:
            active = self._loop.register.active
            return {
                "role": self._role,
                "model": self._model,
                "thread_started": self._thread is not None,
                "closed": self._closed,
                "degradation": self._degradation,
                "counts": dict(self._counts),
                "degradations": list(self._ledger),
                "limits": asdict(self._limits),
                # The two quantities the accounting identities need, so a host
                # can close them without reaching for a private attribute.
                "snapshots_pending": 1 if self._pending is not None else 0,
                "reviews_buffered": len(self._ready),
                "active_scope_id": active.scope_id if active is not None else None,
                "active_version": active.version if active is not None else None,
                # The measurement surface. ``None`` where nothing was measured —
                # never a zero standing in for a number nobody took.
                "relative_latency": self._relative_latency(),
                "max_observed_lag": max(self._observed_lags) if self._observed_lags else None,
                "mean_observed_lag": (
                    sum(self._observed_lags) / len(self._observed_lags)
                    if self._observed_lags
                    else None
                ),
            }

    # ── the worker ───────────────────────────────────────────────────────────
    def _work(self) -> None:
        """The ONE thread's body: take a snapshot, review it, repeat.

        Exits on the stop signal only. Everything inside
        :meth:`~embodiment.scope.ScopeLoop.review` is already guaranteed not to
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
                    self._wake.wait(self._limits.poll_interval)
                    self._wake.clear()
                    continue
                self._absorb(self._loop.review(work.snapshot, step_index=work.step_index))
        except Exception as exc:  # a dead worker is recorded, never silent
            with self._lock:
                self._degrade(DEGRADED_WORKER, f"{type(exc).__name__}: {exc}")
        finally:
            self._idle.set()

    def _take(self) -> Optional[_Work]:
        """Claim the queued snapshot, or mark the lane idle. Worker thread only."""
        with self._lock:
            work = self._pending
            self._pending = None
            if work is None:
                self._idle.set()
                return None
            self._counts["reviews_started"] += 1
            self._last_review_step = work.step_index
            return work

    def _absorb(self, outcome: ScopeOutcome) -> None:
        """Fold one finished review: buffer it, relay its records, count its cost.

        Worker thread only. **The order is load-bearing**: the outcome is
        buffered BEFORE the failure ladder runs, so a review that degraded still
        reaches the host as the authority record saying so, rather than being
        dropped late by the very degradation it reported.
        """
        with self._lock:
            self._counts["reviews_completed"] += 1
            # What the review spent on tools, if it had any. Read defensively
            # like every other host-facing value on this thread: a telemetry
            # field must not be the thing that kills the worker.
            self._counts["tool_rounds"] += _coerce_int(_read(outcome, "tool_rounds", 0))
            latency = _read(outcome, "latency")
            if latency is not None:
                self._review_times.append(_coerce_float(latency, 0.0))
            for degradation in _read(outcome, "degradations", ()) or ():
                self._record(
                    _read(degradation, "code", ""),
                    _read(degradation, "reason", ""),
                    step_index=_coerce_int(_read(degradation, "step_index", 0)),
                    model_turns=_coerce_int(_read(degradation, "model_turns", 0)),
                )
            directive = _read(outcome, "directive")
            if directive is not None:
                self._latest_version = max(
                    self._latest_version, _coerce_int(_read(directive, "version"))
                )
            self._buffer(outcome)
            self._note_failure(outcome)

    def _buffer(self, outcome: ScopeOutcome) -> None:
        """Hold one finished review for the next drain. Call with the lock held."""
        if self._closed or self._stop.is_set():
            self._drop_late_review(outcome, "produced after the runner closed")
            return
        if len(self._ready) == self._ready.maxlen:
            self._counts["reviews_dropped_overflow"] += 1
            self._record(
                DROPPED_OVERFLOW,
                f"drain buffer full ({self._ready.maxlen}); the oldest review was discarded "
                "so the freshest strategic reading survives. The actor is not draining",
                step_index=_coerce_int(_read(outcome, "step_index", 0)),
                model_turns=_coerce_int(_read(outcome, "turns", 0)),
            )
        self._ready.append(outcome)

    def _note_failure(self, outcome: ScopeOutcome) -> None:
        """Advance or reset the consecutive-failure run. Call with the lock held."""
        failed = _read(outcome, "exit_reason") == SCOPE_EXIT_DEGRADED
        if failed and _read(outcome, "directive") is None:
            self._failures += 1
            if self._failures >= self._limits.max_failed_reviews:
                self._degrade(
                    DEGRADED_SEAM,
                    f"{self._failures} consecutive strategic reviews produced nothing; the "
                    "lane stops rather than re-dialling a dead seam at every boundary",
                )
            return
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

    def _drop_late_review(self, outcome: Any, why: str) -> None:
        """Record one finished review with nowhere left to go. Lock held."""
        self._counts["reviews_dropped_late"] += 1
        self._record(
            DROPPED_LATE,
            f"a review of step {_coerce_int(_read(outcome, 'step_index', 0))} was lost: {why}",
            step_index=_coerce_int(_read(outcome, "step_index", 0)),
            model_turns=_coerce_int(_read(outcome, "turns", 0)),
        )

    def _drop_late_snapshot(self, work: _Work, why: str) -> None:
        """Record one snapshot that will never be reviewed. Lock held."""
        self._counts["snapshots_dropped_late"] += 1
        self._record(
            DROPPED_LATE,
            f"snapshot {_label(work.snapshot)!r} was never reviewed: {why}",
            step_index=work.step_index,
        )

    def _record(self, code: str, reason: str, *, step_index: int = 0, model_turns: int = 0) -> None:
        """Append one transition. The counter is exact; the ledger keeps the tail.

        Reuses :class:`~embodiment.scope.ScopeDegradation` rather than minting a
        second degradation shape, so a host's ledger folds ONE record type from
        the review loop and this runner alike (task t3).
        """
        self._counts["degradations_recorded"] += 1
        self._ledger.append(
            ScopeDegradation(
                code=str(code),
                reason=str(reason)[:_MAX_REASON_LEN],
                step_index=_coerce_int(step_index),
                model_turns=_coerce_int(model_turns),
            )
        )

    # ── the measurement surface ──────────────────────────────────────────────
    def note_actor_step(self, seconds: Any) -> None:
        """Tell the runner how long one acting-loop step took.

        The actor half of the relative-latency measurement. The runner cannot
        observe this itself — it has no view of the acting loop — so a host that
        wants :meth:`state`'s ``relative_latency`` populated feeds its own
        measured step durations here. A host that does not call this gets
        ``None``, which is the honest answer rather than a default standing in
        for a measurement nobody made.

        This exists because the muse's staleness constant went stale invisibly:
        nothing in that lane measured the ratio the constant encoded, so the
        assumption behind it survived being falsified. Task t10 re-derives this
        lane's constants from exactly these numbers.

        Never raises: an unreadable or non-positive value is ignored, because a
        telemetry call must not be able to break a drive.
        """
        try:
            value = float(seconds)
        except (TypeError, ValueError):
            return
        # NaN is rejected EXPLICITLY rather than as a side effect of comparison
        # order: every comparison against NaN is False, so folding it into an
        # inverted comparison silently lets it through, and one NaN poisons
        # every later mean in ``_relative_latency``.
        if math.isnan(value) or value <= 0:
            return
        with self._lock:
            self._actor_step_times.append(value)

    def _relative_latency(self) -> Optional[float]:
        """Mean review time over mean acting-step time, or ``None`` without data.

        A value above 1.0 means the strategist is slower than the actor, which is
        this seat's expected profile and the opposite of what the muse measured
        (~0.28, i.e. ~3.5x faster). Reported rather than assumed, because the
        assumed version of exactly this number is what went stale in the muse.
        """
        if not self._review_times or not self._actor_step_times:
            return None
        review_mean = sum(self._review_times) / len(self._review_times)
        actor_mean = sum(self._actor_step_times) / len(self._actor_step_times)
        if review_mean == 0 or actor_mean == 0:
            return None
        return review_mean / actor_mean


def _resolve_limits(limits: Any) -> StrategistLimits:
    """Coerce a host's limits object into a usable one. Never raises.

    A partially built or hostile object degrades field by field to the derived
    defaults rather than breaking a drive — the same never-raise discipline every
    other host-supplied value on this seam gets. ``max_pending`` floors at 1
    because a zero-length buffer would discard every review at the moment it was
    produced, which is a configuration nobody means.
    """
    if limits is None:
        return StrategistLimits()
    return StrategistLimits(
        max_pending=max(1, _coerce_int(_read(limits, "max_pending"), DEFAULT_MAX_PENDING)),
        max_lag=max(0, _coerce_int(_read(limits, "max_lag"), DEFAULT_MAX_LAG)),
        max_failed_reviews=max(
            1, _coerce_int(_read(limits, "max_failed_reviews"), DEFAULT_MAX_FAILED_REVIEWS)
        ),
        join_timeout=_coerce_float(_read(limits, "join_timeout"), DEFAULT_JOIN_TIMEOUT),
        poll_interval=_coerce_float(_read(limits, "poll_interval"), DEFAULT_POLL_INTERVAL),
        review_gap=max(0, _coerce_int(_read(limits, "review_gap"), DEFAULT_REVIEW_GAP)),
    )
