"""The config reviewer's THREAD — one daemon pump beside the acting loop (task t9).

:mod:`embodiment.config_review` is the config strategist's *reasoning*: pure
shapes, typed change admission and a bounded four-exit review loop with no
thread, no clock and no timer. This module is the other half, and only the other
half — the **thread mechanics** that let that loop run beside the acting loop
instead of inside it. Nothing here reasons; nothing there concurs.

CITED, not imported
-------------------
The mechanics below are ``embodiment/strategist_runner.py``'s — which were in
turn ``embodiment/muse_runner.py``'s — copied into this file and then **owned
outright**. That is the cite-don't-import policy this repo runs on, and it is
the reason ``muse_runner.py`` had to stay openable when the muse was archived:
a citation that resolves to a file nobody can read is not a citation.

So this lane imports nothing from :mod:`embodiment.scope`,
:mod:`embodiment.strategist_runner` or :mod:`embodiment.scoped_run`
(``tests/test_config_run.py`` pins it), and the reason is sharper than
tidiness: the advisory lane must stay **byte-stable** as the comparator arm task
``t13`` measures this one against, and an import edge is a reason to edit it.

Because nothing depends on this copy, every name in it was free to change — and
three surfaces were **deleted** rather than carried, each on the spec's own
authority.

Delta 1 — no staleness, because scope ages and configuration does not
----------------------------------------------------------------------
The cited runner dropped a finished review that had fallen more than
``max_lag`` acting steps behind, and derived that threshold from
``ceil(T_review_max / T_actor_step)``. The spec's kept/deleted list retires it:
*"deleted — ... and step-lag staleness"*. The reason is not thrift. A directive
was scope the actor would immediately be **governed by**, so a directive
reasoned about a world that no longer exists is a live hazard. A change unit is
a **proposal**: it cannot take effect until
:class:`~embodiment.config_lifecycle.ConfigLifecycle` has run a verification
suite against the candidate configuration *and* found the seat idle. Staleness
is therefore already handled, downstream, by the mechanism whose whole job it is
— :data:`~embodiment.config_lifecycle.CHANGE_STALE_VERIFICATION` demotes a
proposal whose evidence is about a baseline that has moved. A second, weaker,
step-counting staleness check here would discard proposals the gate would have
graded properly, and would do it without ever running the suite that could tell.

Delta 2 — no supersession, because there is no chain
------------------------------------------------------
The cited runner withheld a buffered directive that a later review had already
overtaken, judged against the register's version chain. The spec deletes *"the
register's chain/version/supersession semantics"*, and the identity discipline
that replaces it is ``change_id``-must-be-new, enforced in one place — the
lifecycle's :meth:`~embodiment.config_lifecycle.ConfigLifecycle.propose`. There
is no version here to compare, and inventing one so this module could refuse
something would be re-importing the defect class (#54) the redesign dissolves.

Delta 3 — the output is a PROPOSAL, not an authority event
------------------------------------------------------------
A lost strategic directive was a lost *authority event*: a decision that would
have changed what the system was trying to do. A lost change unit is a lost
**proposal** — something that would have been graded, and might have been
refused. That is a smaller loss, and it is still recorded, because C3 does not
scale its requirement to the size of the thing lost. Every way a result can fail
to reach the composition layer has its own code, its own exact counter, and a
place in one of two accounting identities that ``tests/test_config_runner.py``
closes arithmetically:

* the **intake** identity — every snapshot offered is either reviewed, displaced
  from the pending slot, skipped by cadence, dropped late (offered to a closed
  or dead lane), or still queued right now;
* the **outcome** identity — every review completed is either delivered, dropped
  late, overflowed out of the buffer, or still buffered right now.

A number that does not close is a silent loss, and the test says which.

The derived defaults, and what carried across
-----------------------------------------------
The three quantities are the cited runner's, and they carry **because the seat
and the turn shape are the same**: the reference rig seats this reviewer on the
gateway's ``cortex`` role, a review is at most
:attr:`~embodiment.config_review.ConfigControls.max_turns` (3) tools-off turns,
and a turn is roughly a thousand reasoning tokens plus a short JSON batch. From
``docs/live-test-results/timeout-rate-measurements.json``:

``T_review_min``
    One review turn at the *fastest* measured cortex rate:
    ``(1000 reasoning + 300 batch) tokens / 25.4 tok/s`` = **51 s**.
``T_review_max``
    A full review at the *slowest* measured cortex rate, plus the committed
    non-generation allowance for queue wait and prefill:
    ``3 turns x 1300 tokens / 21.5 tok/s + 179.3 s`` = **361 s**.
``T_actor_step``
    One acting step on the worker seat at its mean measured rate:
    ``1200 tokens / 38.9 tok/s`` = **31 s**.

Those are ratios of committed measurements, not preferences. What is *not*
carried is the one default those quantities were used to derive that this lane
no longer has: ``DEFAULT_MAX_LAG``. Deleting the mechanism deleted its constant,
rather than leaving a number nothing reads.

The lane instruments ITSELF, for the reason the muse's constant went stale
---------------------------------------------------------------------------
:meth:`ConfigRunner.note_actor_step` and :meth:`ConfigRunner.state`'s
``relative_latency`` exist so a live run hands the next re-derivation real
numbers instead of the arithmetic above. A host that measures nothing gets
``None``, which is the honest answer rather than a default standing in for a
measurement nobody took.

The thread discipline, inherited from ``colleague/realtime.py``
---------------------------------------------------------------
* **ONE daemon thread.** No pool, no second pump, no thread per review.
* A :class:`threading.Event` **stop signal**, plus a wake event so teardown is
  prompt rather than parked on a poll interval.
* A **poll-wake wait**: the worker wakes on the event *or* on a bounded poll
  interval, and re-checks the stop flag either way, so a missed wakeup costs
  latency and never a hang.
* :func:`_bounded_join` — teardown **never hangs**, even when the reviewer is
  parked inside a model call nothing can interrupt. The thread is a daemon
  precisely so an unreapable review cannot keep a host's process alive.
* **Degrade, never raise.** A start failure, a dead seam, a mid-run failure and
  a worker that dies each flip a degraded flag and record a transition. No
  exception ever crosses back into the acting loop's main path.

No actor authority — structurally
-----------------------------------
This module drives a :class:`~embodiment.config_review.ConfigReviewLoop` and
hands back :class:`~embodiment.config_review.ConfigOutcome` values, which carry
typed change units and provenance and nothing else. It builds no executor,
constructs no tool and holds no decision vocabulary in scope: it never imports
:mod:`embodiment.loop`. There is no path from anything here to a tool-call
decision, and that is held by the mechanism rather than by promise.

Observability is PULL-only
--------------------------
:meth:`ConfigRunner.state`, :attr:`~ConfigRunner.degradations` and
:attr:`~ConfigRunner.counts` are folds of what already happened. There is
deliberately no ``on_degradation`` callback and no listener registry: firing
host code from this thread is exactly how a "never blocks the actor" promise
gets quietly broken, and the event stream is
:mod:`embodiment.config_run`'s job through the host's own observer.

Stdlib only (``collections``, ``dataclasses``, ``math``, ``threading``,
``typing``) beyond :mod:`embodiment.config_review` (constraint C1).
"""

from __future__ import annotations

import math
import threading
from collections import deque
from dataclasses import asdict, dataclass
from typing import Any, Callable, Iterable, Optional

from embodiment.capability import CapabilityCatalog
from embodiment.config_change import ConfigDegradation
from embodiment.config_review import (
    CONFIG_EXIT_CHANGES,
    CONFIG_EXIT_DEGRADED,
    CONFIG_EXIT_UNCHANGED,
    REVIEW_CODES,
    ConfigCompleteFn,
    ConfigControls,
    ConfigOutcome,
    ConfigReviewLoop,
    ConfigSnapshot,
)

__all__ = [
    # role + thread identity
    "REVIEWER_ROLE",
    "THREAD_NAME",
    # the transition vocabulary this lane MINTS (C3)
    "RUNNER_DEGRADED_THREAD",
    "RUNNER_DEGRADED_WORKER",
    "RUNNER_DEGRADED_SEAM",
    "RUNNER_DEGRADED_CLOSER",
    "RUNNER_DROPPED_LATE",
    "RUNNER_DROPPED_OVERFLOW",
    "RUNNER_DROPPED_SNAPSHOT",
    "RUNNER_DROPPED_CADENCE",
    "RUNNER_CODES",
    "LANE_CODES",
    # defaults, each derived in a comment above it
    "DEFAULT_MAX_PENDING",
    "DEFAULT_MAX_FAILED_REVIEWS",
    "DEFAULT_JOIN_TIMEOUT",
    "DEFAULT_POLL_INTERVAL",
    "DEFAULT_REVIEW_GAP",
    "MAX_LEDGER",
    # the runner
    "ConfigLimits",
    "ThreadFactory",
    "Closer",
    "ConfigRunner",
]


# ── identity ──────────────────────────────────────────────────────────────────

#: The ROLE this runner drives, by name. Roles resolve by name from a host's own
#: capability contract — never by parsing a model name (there is no model name
#: anywhere in this module, on purpose). The reference rig seats this on the
#: gateway's ``cortex`` role, but that mapping is the HOST's configuration and
#: this label is only what the record says.
REVIEWER_ROLE = "strategist"

#: The one thread's name. Fixed so a host's stack dump or profiler names it.
THREAD_NAME = "embodiment-config-reviewer"


# ── the transition vocabulary this lane MINTS (C3) ────────────────────────────
#
# Every code is prefixed ``config-runner-`` so the ledger harvest cannot collide
# with ``config-change-`` (t3/t4), ``config-ledger-`` (t5) or
# ``config-review-`` — even though all of them fold into ONE
# ConfigDegradation stream. Every one has a producer in this file plus a test
# that fires it (embodiment#18: a code nothing can mint is a lie in the ledger).

#: The worker thread could not be started; the lane never runs at all.
RUNNER_DEGRADED_THREAD = "config-runner-thread-unavailable"
#: The worker itself died on an unexpected exception. It should be unreachable —
#: :meth:`embodiment.config_review.ConfigReviewLoop.review` never raises — so if
#: it fires, the record is the only thing standing between a bug and a silently
#: absent reviewer.
RUNNER_DEGRADED_WORKER = "config-runner-worker-failed"
#: Consecutive reviews failed outright; the lane stops dialling. Distinct from a
#: single :data:`~embodiment.config_review.REVIEW_DEGRADED_SEAM`, which is one
#: review's fault and is relayed.
RUNNER_DEGRADED_SEAM = "config-runner-seam-dead"
#: A host-wired teardown callable raised at :meth:`ConfigRunner.close`. The lane
#: is already stopping, so this never propagates — but a teardown that did not
#: happen is exactly the kind of loss C3 refuses to leave unsaid.
RUNNER_DEGRADED_CLOSER = "config-runner-closer-failed"
#: A review arrived (or was still buffered) after the runner closed, or a
#: snapshot was offered to a lane that had already closed or degraded. Either
#: way a proposal had nowhere to go.
RUNNER_DROPPED_LATE = "config-runner-review-late"
#: The drain buffer was full; the oldest review was discarded to make room, so
#: the freshest reading of the rig survives.
RUNNER_DROPPED_OVERFLOW = "config-runner-review-overflow"
#: A queued snapshot was replaced by a newer one before it was ever reviewed.
#: The single pending slot's whole purpose, and its cost, recorded.
RUNNER_DROPPED_SNAPSHOT = "config-runner-snapshot-displaced"
#: A snapshot arrived inside the cadence gap and no review was started for it.
#: Deliberately a recorded drop rather than a silent no-op: skipping a review is
#: a decision not to look at the configuration at that boundary.
RUNNER_DROPPED_CADENCE = "config-runner-review-skipped"

#: The complete set of codes this module MINTS. Eight — the cited runner's ten,
#: minus the two the deleted mechanisms took with them (see Delta 1 and 2).
RUNNER_CODES = (
    RUNNER_DEGRADED_THREAD,
    RUNNER_DEGRADED_WORKER,
    RUNNER_DEGRADED_SEAM,
    RUNNER_DEGRADED_CLOSER,
    RUNNER_DROPPED_LATE,
    RUNNER_DROPPED_OVERFLOW,
    RUNNER_DROPPED_SNAPSHOT,
    RUNNER_DROPPED_CADENCE,
)

#: Everything a record on this lane can carry: what this module mints, plus the
#: review-level vocabulary :meth:`ConfigRunner._absorb` copies verbatim from a
#: finished :class:`~embodiment.config_review.ConfigOutcome`. Declared here so a
#: ledger reader takes ONE vocabulary from ONE module rather than reconciling
#: two. Admission refusals ride the same stream and keep ``t3``'s own
#: ``config-change-`` codes.
LANE_CODES = RUNNER_CODES + REVIEW_CODES


# ── the derived defaults ──────────────────────────────────────────────────────
#
# Read the module docstring first: T_review_min = 51 s, T_review_max = 361 s and
# T_actor_step = 31 s are all ratios of figures committed in
# docs/live-test-results/timeout-rate-measurements.json. Nothing below is a
# number someone liked; each is that arithmetic, stated where it is used.

#: How many undrained reviews the buffer holds before discarding the oldest.
#: DERIVATION: the producer is strictly slower than the consumer here. The lane
#: emits at most one review per T_review_min = 51 s (one turn at the fastest
#: measured cortex rate, 25.4 tok/s), and the composition drains at every acting
#: step, T_actor_step = 31 s apart — so a depth of 2 already cannot be exceeded
#: by a healthy lane. 4 is 2x that, headroom for a host that drains coarsely. A
#: larger buffer would be dead capacity AND would delay the overflow record,
#: which is the part that matters: an overflow means the composition stopped
#: draining, and a small buffer surfaces that in one review instead of thirty.
DEFAULT_MAX_PENDING = 4
#: Consecutive failed reviews tolerated before the lane stops dialling.
#: DERIVATION: one failed review can occupy the seam for the whole request
#: bound, T_review_max = 361 s, so re-dialling a dead seam at every boundary
#: costs minutes of a drive rather than seconds. One failure is therefore enough
#: evidence to stop. A host that expects a flaky link raises it deliberately.
DEFAULT_MAX_FAILED_REVIEWS = 1
#: Bound on :meth:`ConfigRunner.close`'s join. Teardown never hangs.
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
#: unit, which is noise.
DEFAULT_POLL_INTERVAL = 1.0
#: Minimum acting steps between review STARTS — the cadence gap.
#: DERIVATION: ceil(T_review_min / T_actor_step) = ceil(51 s / 31 s) = 2. The
#: lane never starts a second review before the fastest possible one could have
#: finished, so a deep thinker is not offered work it provably cannot have
#: absorbed yet. This matters in tokens as well as time: live session 1 measured
#: 69.5% of a strategist's 28,318 tokens buying restatement or nothing, and
#: reviewing at every boundary is how that arithmetic gets worse. A snapshot
#: carrying an explicit ``requested_decision`` always passes — an escalation is
#: the host asking a question, not a cadence tick. ``0`` disables the gap.
DEFAULT_REVIEW_GAP = 2
#: How many of the most recent transitions the ledger keeps. The counters stay
#: exact past it, and the lane's terminal degradation lives in its own field.
#: DERIVATION: at most a handful of records per review, and a review costs at
#: least T_review_min = 51 s, so 100 records span upward of 85 minutes of a
#: healthy lane. The bound exists so a pathological lane cannot grow it without
#: limit, not to ration a normal one.
MAX_LEDGER = 100

#: Cap on one record's reason text, mirroring the rest of the tier.
_MAX_REASON_LEN = 500

#: Builds the worker thread. Injected so a test can assert a reviewerless run
#: creates none, and so a host with its own thread policy can supply one.
ThreadFactory = Callable[..., Any]

#: One host-owned teardown, tied to this lane's close. A zero-argument callable
#: and **nothing more** — the runner never inspects it, never learns what it
#: closes, and imports nothing to accommodate it. Ownership stays where
#: construction is; only the *timing* is delegated here.
Closer = Callable[[], Any]


@dataclass(frozen=True)
class ConfigLimits:
    """The lane's five tuning scalars, in one default-constructed object.

    Five rather than the cited runner's six: ``max_lag`` went with the staleness
    mechanism (Delta 1). Every field is read through :func:`_coerce_int` /
    :func:`_coerce_float`, so a host handing in a partially-built or hostile
    object degrades to the derived defaults rather than breaking a drive.

    Fields
    ------
    max_pending:
        Undrained reviews held before the oldest is discarded (and recorded).
    max_failed_reviews:
        Consecutive failed reviews tolerated before the lane stops dialling.
    join_timeout:
        The default bound on :meth:`ConfigRunner.close`'s join.
    poll_interval:
        The worker's poll-wake bound (a safety net, not a clock).
    review_gap:
        Minimum acting steps between review starts. ``0`` disables cadence.
    """

    max_pending: int = DEFAULT_MAX_PENDING
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
    # isfinite rejects NaN *and* both infinities. An infinite join_timeout would
    # defeat the bounded-join guarantee this module exists to hold — a shutdown
    # that waits forever is the failure a bounded join is named for.
    if not math.isfinite(number) or number <= 0:
        return default
    return number


def _read(obj: Any, name: str, default: Any = None) -> Any:
    """``getattr`` that cannot raise — a hostile property reads as absent.

    Every snapshot field this module touches goes through here, because
    :meth:`ConfigRunner.consider` runs on the ACTOR's thread: an exception
    raised reading a host's object would be an exception in the acting loop's
    main path, which is the one thing a presence layer may never do. The review
    itself reads the snapshot again, defensively, one layer down.
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
    """One unit of reviewer work: what to review, and where the actor was.

    Private on purpose. A host offers work through :meth:`ConfigRunner.consider`
    and the runner stamps the step; there is no shape here for a host to
    construct. Frozen so a queued item cannot be edited after the cadence and
    displacement decisions that admitted it.
    """

    snapshot: Any
    step_index: int = 0


class ConfigRunner:
    """Run ONE :class:`~embodiment.config_review.ConfigReviewLoop` on one thread.

    ``consider`` offers a snapshot and returns; ``drain`` collects whatever
    finished; ``degradation`` reports the lane having stopped. None of the three
    blocks, and none of them raises — including against a seam that never
    returns at all.

    Args:
        complete: the injected reviewer seam
            (:data:`~embodiment.config_review.ConfigCompleteFn`), already
            carrying its own endpoint. This module never infers a model, a role
            or an address.
        role: the role NAME this lane drives, recorded for provenance. It names
            who is reviewing, never what may be inferred from it.
        model: the model id the seam is configured to call, for the record.
            Host-declared and empty by default — which is what keeps a
            single-model run from claiming a strategist exists (colleague#352).
        controls: the review loop's turn budget and caps.
        catalog: the host's capability declaration, handed to the review loop and
            used for nothing else here.
        system: OPTIONAL host framing, appended to the reviewer's authority
            boundary (never substituted for it).
        clock: the ONLY source of a latency measurement, handed to the review
            loop. Absent, every ``latency`` stays ``None`` rather than a
            fabricated zero — and :meth:`state`'s ``relative_latency`` stays
            ``None`` with it.
        limits: the five tuning scalars (:class:`ConfigLimits`). Omitted, the
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

        with ConfigRunner(complete) as reviewer:      # no thread yet
            reviewer.consider(projector(), step_index=step)
            for outcome in reviewer.drain(step_count=step):
                for change in outcome.changes:
                    lifecycle.propose(change)          # a PROPOSAL, not an effect
        # closed: stop signalled, join bounded, undrained reviews recorded

    The runner must not outlive the drive: :meth:`close` is idempotent, bounded,
    and safe to call from anywhere including the worker thread itself (the join
    skips the current thread rather than deadlocking).
    """

    def __init__(
        self,
        complete: ConfigCompleteFn,
        *,
        role: str = REVIEWER_ROLE,
        model: str = "",
        controls: Optional[ConfigControls] = None,
        catalog: Optional[CapabilityCatalog] = None,
        system: Optional[str] = None,
        clock: Optional[Callable[[], float]] = None,
        limits: Optional[ConfigLimits] = None,
        thread_factory: Optional[ThreadFactory] = None,
        closers: Iterable[Closer] = (),
    ) -> None:
        self._role = str(role or REVIEWER_ROLE)
        self._model = str(model or "")
        # Materialised at construction, not at close: a generator handed here
        # would be consumed by an earlier close and silently empty at the one
        # that matters. Deliberately NOT wrapped in a try — a ``closers`` that is
        # not iterable is a wiring mistake, and failing at construction beats
        # discovering at teardown that nothing was ever going to be closed.
        self._closers: tuple[Closer, ...] = tuple(closers)
        # ONE loop instance, driven by ONE thread, one review at a time.
        self._loop = ConfigReviewLoop(
            complete,
            controls=controls,
            catalog=catalog,
            system=system,
            clock=clock,
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
        self._ready: deque[ConfigOutcome] = deque(maxlen=self._limits.max_pending)
        self._ledger: deque[ConfigDegradation] = deque(maxlen=MAX_LEDGER)
        self._failures = 0
        self._observed_step = 0
        #: Cadence state, and it is deliberately ACTOR-THREAD-ONLY: written and
        #: read in ``_offer``/``_cadence_blocks``, both on the caller's thread and
        #: both under the lock. It used to be the step and count of the most
        #: recently STARTED review, which the WORKER thread wrote in ``_take`` —
        #: so whether a snapshot was gated depended on whether the reviewer had
        #: got round to dequeuing yet. Measured at 11 of 40 unloaded trials
        #: returning 5 skips instead of 6, and red on CI.
        #:
        #: Accept-time is also the more faithful reference point. Cadence rate
        #: limits what the lane is ASKED to review; a snapshot that is accepted
        #: and then displaced by a newer projection still consumed that
        #: opportunity, and displacement is separately counted
        #: (``snapshots_displaced``) rather than lost.
        self._accepted_step = 0
        self._accepted_reviews = 0
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
            "reviews_dropped_late": 0,
            "reviews_dropped_overflow": 0,
            # what the delivered reviews actually said. A hold is a REAL answer
            # and is counted as one: non-intervention is a graded outcome, and a
            # lane that only counted changes would report a careful reviewer as
            # an idle one.
            "changes_delivered": 0,
            "holds_delivered": 0,
            "refusals_relayed": 0,
            # faults
            "degradations_recorded": 0,
        }
        # Relative latency: review times vs acting step times. The review side
        # fills itself from each finished review (see :meth:`_absorb`); the actor
        # side can only come from the host, which is the only party that knows
        # how long its own steps took (see :meth:`note_actor_step`).
        self._review_times: list[float] = []
        self._actor_step_times: list[float] = []

    # ── the drain-shaped seam ────────────────────────────────────────────────
    def consider(self, snapshot: Optional[ConfigSnapshot], *, step_index: int = 0) -> None:
        """Offer one snapshot to review, then return. Never blocks or raises.

        At most one snapshot is ever queued: a snapshot arriving while a review
        is in flight REPLACES any snapshot still waiting, so the reviewer always
        reads the most recent projection rather than working through a backlog of
        stale ones. The displaced snapshot is recorded
        (:data:`RUNNER_DROPPED_SNAPSHOT`), never silently dropped.

        *step_index* is the acting loop's current step. It rides the review and
        every record on it, and it is what the cadence gap measures distance in.
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
                    RUNNER_DROPPED_SNAPSHOT,
                    f"snapshot {_label(self._pending.snapshot)!r} was superseded by a newer "
                    "projection before any review of it began",
                    step_index=self._pending.step_index,
                )
            self._pending = work
            # Accepted: this snapshot has consumed a cadence opportunity, whether
            # or not the worker ever gets to dequeue it. Set here, on the actor's
            # thread and under the same lock the gate reads it under.
            self._accepted_reviews += 1
            self._accepted_step = work.step_index
            self._observed_step = max(self._observed_step, work.step_index)
            self._idle.clear()
        if not self.start():
            # No thread means nothing will ever pick this up; say so rather than
            # leaving a proposal opportunity queued against a lane that does not
            # exist. Recorded against ``work`` rather than against whatever is in
            # the slot: a failing ``start`` degrades the lane, and ``_degrade``
            # has already emptied the slot by the time this runs.
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
        * nothing has been accepted for review yet — the first one is never
          gated;
        * the snapshot carries a ``requested_decision`` — an explicit escalation
          from the host is a question, not a cadence tick;
        * ``step_index <= 0`` — cadence is a step DISTANCE, and a host that
          supplies no steps has none to measure.

        Every value this reads is written on the caller's own thread (see
        :attr:`_accepted_step`), so the decision is a function of the actor's
        step sequence alone. It is not merely deterministic-in-practice: there is
        no worker-thread state left in it to race against.
        """
        gap = self._limits.review_gap
        if gap <= 0 or self._accepted_reviews == 0 or work.step_index <= 0:
            return False
        if _read(work.snapshot, "requested_decision"):
            return False
        if work.step_index <= self._accepted_step:
            # The caller's counter went BACKWARDS, so it is a per-drive index
            # that restarted rather than a monotonic one — `run_configured`
            # supplies exactly that. Measuring a gap against a reference point
            # from a previous drive makes the difference negative and blocks
            # every review for the rest of the process: seam trap T2, measured
            # at six drives producing one review (embodiment#79). A restart is
            # a new sequence, so the reference point restarts with it.
            self._accepted_step = 0
        if (work.step_index - self._accepted_step) >= gap:
            return False
        self._counts["snapshots_skipped_cadence"] += 1
        self._record(
            RUNNER_DROPPED_CADENCE,
            f"snapshot {_label(work.snapshot)!r} arrived {work.step_index - self._accepted_step}"
            f" step(s) after the last accepted review; the cadence gap is {gap}",
            step_index=work.step_index,
        )
        return True

    def drain(self, *, step_count: int = 0) -> list[ConfigOutcome]:
        """Return whatever reviews are ready NOW. Never blocks or raises.

        An empty list is the normal case — the reviewer works on its own clock
        and an acting loop must never wait on it.

        **Nothing is withheld here.** The cited runner dropped stale and
        superseded directives at exactly this point; both mechanisms are deleted
        (see the module docstring's Delta 1 and 2), and what replaces them is the
        lifecycle's gate, which grades a proposal against the configuration
        actually in force at the moment it would apply. *step_count* is therefore
        read only to keep the lane's own step observation current.
        """
        with self._lock:
            self._observed_step = max(self._observed_step, _coerce_int(step_count))
            ready = list(self._ready)
            self._ready.clear()
            for outcome in ready:
                self._note_delivery(outcome)
            self._counts["reviews_delivered"] += len(ready)
            return ready

    def _note_delivery(self, outcome: ConfigOutcome) -> None:
        """Count what one delivered review said. Call with the lock held."""
        exit_reason = _read(outcome, "exit_reason")
        if exit_reason == CONFIG_EXIT_CHANGES:
            changes = _read(outcome, "changes", ()) or ()
            self._counts["changes_delivered"] += len(tuple(changes))
        elif exit_reason == CONFIG_EXIT_UNCHANGED:
            self._counts["holds_delivered"] += 1

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

        Note the liveness check on an existing thread: returning ``True`` for any
        non-``None`` thread would report a worker that had already died as
        healthy for the rest of the process, and every later :meth:`consider`
        would queue into a lane that never reads it (found by Qodo on PR #76 in
        the cited runner, and carried across rather than re-earned).
        """
        with self._lock:
            if self._closed or self._degradation is not None:
                return False
            if self._thread is not None:
                if self._thread.is_alive():
                    return True
                self._degrade(
                    RUNNER_DEGRADED_WORKER,
                    "the config reviewer thread is no longer alive; the lane cannot "
                    "process queued snapshots and will not be restarted",
                )
                return False
            try:
                thread = self._thread_factory(target=self._work, name=THREAD_NAME, daemon=True)
                thread.start()
            except Exception as exc:  # no thread is a degradation, not a crash
                self._degrade(RUNNER_DEGRADED_THREAD, f"{type(exc).__name__}: {exc}")
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
                        RUNNER_DEGRADED_CLOSER,
                        f"a teardown wired to this lane's close raised and did not "
                        f"finish: {type(exc).__name__}: {exc}",
                    )

    def wait_idle(self, timeout: float) -> bool:
        """Block until no review is in flight and none is queued.

        A bounded wait on a real condition, for orderly shutdown and for tests —
        which is why the concurrency tests need no sleeps. **The acting loop must
        never call this on its hot path**: waiting on the reviewer is the one
        thing this lane exists to avoid. Returns ``False`` on timeout.
        """
        return self._idle.wait(timeout)

    def __enter__(self) -> "ConfigRunner":
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
        """Whether a worker thread was ever created. A reviewerless run: never."""
        return self._thread is not None

    @property
    def degradations(self) -> list[ConfigDegradation]:
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
                "reviews": self._loop.reviews,
                # The measurement surface. ``None`` where nothing was measured —
                # never a zero standing in for a number nobody took.
                "relative_latency": self._relative_latency(),
            }

    # ── the worker ───────────────────────────────────────────────────────────
    def _work(self) -> None:
        """The ONE thread's body: take a snapshot, review it, repeat.

        Exits on the stop signal only. Everything inside
        :meth:`~embodiment.config_review.ConfigReviewLoop.review` is already
        guaranteed not to raise, so the outer guard here is for this module's own
        bugs: a worker that dies must leave a record, not an absent reviewer the
        host mistakes for a quiet one.

        The guard catches :class:`BaseException`, not :class:`Exception`, and the
        difference is load-bearing. A ``SystemExit``, a ``KeyboardInterrupt``, or
        a host cancellation exception inheriting ``BaseException`` would
        otherwise kill this thread with **no record written** — precisely the
        absent reviewer the paragraph above forbids. The record is written and
        the exception is then **re-raised**, so interpreter shutdown and Ctrl-C
        keep their meaning.
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
                self._degrade(RUNNER_DEGRADED_WORKER, f"{type(exc).__name__}: {exc}")
        # Recorded, then re-raised on the line below — the record must not cost
        # the exception its meaning.
        except BaseException as exc:
            with self._lock:
                self._degrade(RUNNER_DEGRADED_WORKER, f"{type(exc).__name__}: {exc}")
            raise
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
            return work

    def _absorb(self, outcome: ConfigOutcome) -> None:
        """Fold one finished review: buffer it, relay its records, count its cost.

        Worker thread only. **The order is load-bearing**: the outcome is
        buffered BEFORE the failure ladder runs, so a review that degraded still
        reaches the composition as the record saying so, rather than being
        dropped late by the very degradation it reported.
        """
        with self._lock:
            self._counts["reviews_completed"] += 1
            latency = _read(outcome, "latency")
            if latency is not None:
                self._review_times.append(_coerce_float(latency, 0.0))
            for degradation in _read(outcome, "degradations", ()) or ():
                self._record(
                    _read(degradation, "code", ""),
                    _read(degradation, "reason", ""),
                    step_index=_coerce_int(_read(degradation, "step_index", 0)),
                    model_turns=_coerce_int(_read(degradation, "model_turns", 0)),
                    seat=_read(degradation, "seat", "") or "",
                    target=_read(degradation, "target", "") or "",
                )
            self._counts["refusals_relayed"] += len(tuple(_read(outcome, "refusals", ()) or ()))
            self._buffer(outcome)
            self._note_failure(outcome)

    def _buffer(self, outcome: ConfigOutcome) -> None:
        """Hold one finished review for the next drain. Call with the lock held."""
        if self._closed or self._stop.is_set():
            self._drop_late_review(outcome, "produced after the runner closed")
            return
        if len(self._ready) == self._ready.maxlen:
            # Attribute the drop to the review actually lost. ``self._ready`` is
            # a bounded deque, so ``append`` evicts the LEFTMOST entry — not the
            # one arriving. Reading the arriving outcome's fields would make the
            # record contradict its own prose (the cited runner's #56-family bug,
            # found by Qodo on PR #76).
            evicted = self._ready[0]
            self._counts["reviews_dropped_overflow"] += 1
            self._record(
                RUNNER_DROPPED_OVERFLOW,
                f"drain buffer full ({self._ready.maxlen}); the oldest review was discarded "
                "so the freshest reading survives. The composition is not draining",
                step_index=_coerce_int(_read(evicted, "step_index", 0)),
                model_turns=_coerce_int(_read(evicted, "turns", 0)),
            )
        self._ready.append(outcome)

    def _note_failure(self, outcome: ConfigOutcome) -> None:
        """Advance or reset the consecutive-failure run. Call with the lock held."""
        failed = _read(outcome, "exit_reason") == CONFIG_EXIT_DEGRADED
        if failed and not tuple(_read(outcome, "changes", ()) or ()):
            self._failures += 1
            if self._failures >= self._limits.max_failed_reviews:
                self._degrade(
                    RUNNER_DEGRADED_SEAM,
                    f"{self._failures} consecutive configuration reviews produced nothing; "
                    "the lane stops rather than re-dialling a dead seam at every boundary",
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
            RUNNER_DROPPED_LATE,
            f"a review of step {_coerce_int(_read(outcome, 'step_index', 0))} was lost: {why}",
            step_index=_coerce_int(_read(outcome, "step_index", 0)),
            model_turns=_coerce_int(_read(outcome, "turns", 0)),
        )

    def _drop_late_snapshot(self, work: _Work, why: str) -> None:
        """Record one snapshot that will never be reviewed. Lock held."""
        self._counts["snapshots_dropped_late"] += 1
        self._record(
            RUNNER_DROPPED_LATE,
            f"snapshot {_label(work.snapshot)!r} was never reviewed: {why}",
            step_index=work.step_index,
        )

    def _record(
        self,
        code: str,
        reason: str,
        *,
        step_index: int = 0,
        model_turns: int = 0,
        seat: str = "",
        target: str = "",
    ) -> None:
        """Append one transition. The counter is exact; the ledger keeps the tail.

        Reuses :class:`~embodiment.config_change.ConfigDegradation` rather than
        minting a second degradation shape, so a host's ledger folds ONE record
        type from the review loop, the admission path and this runner alike.
        """
        self._counts["degradations_recorded"] += 1
        self._ledger.append(
            ConfigDegradation(
                code=str(code),
                reason=str(reason)[:_MAX_REASON_LEN],
                step_index=_coerce_int(step_index),
                model_turns=_coerce_int(model_turns),
                seat=str(seat),
                target=str(target),
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
        assumption behind it survived being falsified.

        Never raises: an unreadable or non-positive value is ignored, because a
        telemetry call must not be able to break a drive.
        """
        try:
            value = float(seconds)
        except (TypeError, ValueError):
            return
        # NaN is rejected EXPLICITLY rather than as a side effect of comparison
        # order: every comparison against NaN is False, so folding it into an
        # inverted comparison silently lets it through, and one NaN poisons every
        # later mean.
        if math.isnan(value) or value <= 0:
            return
        with self._lock:
            self._actor_step_times.append(value)

    def _relative_latency(self) -> Optional[float]:
        """Mean review time over mean acting-step time, or ``None`` without data.

        A value above 1.0 means the reviewer is slower than the actor, which is
        this seat's expected profile. Reported rather than assumed, because the
        assumed version of exactly this number is what went stale in the muse.
        """
        if not self._review_times or not self._actor_step_times:
            return None
        review_mean = sum(self._review_times) / len(self._review_times)
        actor_mean = sum(self._actor_step_times) / len(self._actor_step_times)
        if review_mean == 0 or actor_mean == 0:
            return None
        return review_mean / actor_mean


def _resolve_limits(limits: Any) -> ConfigLimits:
    """Coerce a host's limits object into a usable one. Never raises.

    A partially built or hostile object degrades field by field to the derived
    defaults rather than breaking a drive — the same never-raise discipline every
    other host-supplied value on this seam gets. ``max_pending`` floors at 1
    because a zero-length buffer would discard every review at the moment it was
    produced, which is a configuration nobody means.
    """
    if limits is None:
        return ConfigLimits()
    return ConfigLimits(
        max_pending=max(1, _coerce_int(_read(limits, "max_pending"), DEFAULT_MAX_PENDING)),
        max_failed_reviews=max(
            1, _coerce_int(_read(limits, "max_failed_reviews"), DEFAULT_MAX_FAILED_REVIEWS)
        ),
        join_timeout=_coerce_float(_read(limits, "join_timeout"), DEFAULT_JOIN_TIMEOUT),
        poll_interval=_coerce_float(_read(limits, "poll_interval"), DEFAULT_POLL_INTERVAL),
        review_gap=max(0, _coerce_int(_read(limits, "review_gap"), DEFAULT_REVIEW_GAP)),
    )
