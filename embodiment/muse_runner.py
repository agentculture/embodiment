"""The muse's THREAD — one daemon pump beside the actor loop (task t10b).

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
and provenance and nothing else. It has no executor, no tool surface and no
decision vocabulary in scope: it never imports :mod:`embodiment.loop`. There is
no path from anything here to a tool-call decision, and that is held by the
mechanism rather than by promise (`colleague#352
<https://github.com/agentculture/colleague/issues/352>`_).

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

import threading
from collections import deque
from dataclasses import replace
from typing import Any, Callable, Optional, cast

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
    insight_lag,
    is_stale,
)
from embodiment.presence_engine import BoundaryContext, MuseComment

__all__ = [
    # role + thread identity
    "MUSE_ROLE",
    "THREAD_NAME",
    # the transition vocabulary (C3)
    "DEGRADED_THREAD",
    "DEGRADED_WORKER",
    "DEGRADED_ENDPOINT",
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
    # defaults
    "DEFAULT_MAX_PENDING",
    "DEFAULT_MAX_FAILED_SESSIONS",
    "DEFAULT_JOIN_TIMEOUT",
    "DEFAULT_POLL_INTERVAL",
    "MAX_LEDGER",
    # the runner
    "ThreadFactory",
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
#: An insight fell too far behind the actor loop to be worth delivering.
DROPPED_STALE = "muse-insight-stale"
#: An insight arrived (or was still buffered) after the runner closed.
DROPPED_LATE = "muse-insight-late"
#: The drain buffer was full; the oldest insight was discarded to make room.
DROPPED_OVERFLOW = "muse-insight-overflow"
#: A queued boundary was replaced by a newer one before it was ever thought about.
DROPPED_BOUNDARY = "muse-boundary-superseded"
#: Background compilation was starved because boundary counsel took priority.
DROPPED_COMPILATION_STARVED = "muse-compilation-starved"
#: Boundary counsel was displaced by background compilation filling the buffer.
DROPPED_COUNSEL_DISPLACED = "muse-counsel-displaced"

#: The complete set this module can record. Session-level codes
#: (``muse-thinking-failed`` and friends) come through verbatim from
#: :mod:`embodiment.muse`; this runner mints no code outside these nine.
RUNNER_CODES = (
    DEGRADED_THREAD,
    DEGRADED_WORKER,
    DEGRADED_ENDPOINT,
    DROPPED_STALE,
    DROPPED_LATE,
    DROPPED_OVERFLOW,
    DROPPED_BOUNDARY,
    DROPPED_COMPILATION_STARVED,
    DROPPED_COUNSEL_DISPLACED,
)


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

#: Cap on one record's reason text, mirroring :mod:`embodiment.muse`.
_MAX_REASON_LEN = 500

#: Work-class labels for the muse thread's two kinds of work.
WORK_BOUNDARY = "boundary"
WORK_COMPILATION = "compilation"
#: The complete set of work classes.
WORK_CLASSES = (WORK_BOUNDARY, WORK_COMPILATION)

#: Builds the worker thread. Injected so a test can assert a museless run
#: creates none, and so a host with its own thread policy can supply one.
ThreadFactory = Callable[..., Any]


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
    except Exception:  # noqa: BLE001 - an unreadable attribute is simply absent
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


class ThreadedMuseRunner:
    """Run ONE :class:`~embodiment.muse.MuseLoop` on one daemon thread.

    Satisfies the pump's drain-shaped
    :class:`~embodiment.presence_engine.MuseSeam`: ``consider`` offers a
    boundary and returns, ``drain`` collects whatever finished, ``degradation``
    reports the lane having stopped. None of the three blocks, and none of them
    raises.

    Args:
        complete: the injected tools-off thinking seam
            (:data:`~embodiment.muse.MuseCompleteFn`) — the ONE thing here that
            may talk to a network, already carrying its own endpoint. This
            module never infers a model, a role or an address.
        role: the role NAME this lane drives, recorded for provenance. It names
            who is thinking, never what may be inferred from it.
        controls: the thinking loop's turn budget and caps.
        system: OPTIONAL host framing, appended to the muse's authority boundary
            (never substituted for it).
        clock: the ONLY source of a latency measurement, handed to the thinking
            loop. Absent, every ``latency`` stays ``None`` rather than a
            fabricated zero. A runner owns a thread, so it may legitimately own
            a clock too — but only if the host gives it one.
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
        max_pending: int = DEFAULT_MAX_PENDING,
        max_lag: int = DEFAULT_STALE_LAG,
        max_failed_sessions: int = DEFAULT_MAX_FAILED_SESSIONS,
        join_timeout: float = DEFAULT_JOIN_TIMEOUT,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        thread_factory: Optional[ThreadFactory] = None,
        recall_bundle: Any = None,
    ) -> None:
        self._role = str(role or MUSE_ROLE)
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
        self._loop = MuseLoop(
            complete,
            controls=controls,
            system=system,
            sink=self._deliver,
            clock=clock,
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
        self._pending: Optional[BoundaryContext] = None
        self._ready: deque[MuseInsight] = deque(maxlen=max(1, _coerce_int(max_pending, 1)))
        self._ledger: deque[MuseDegradation] = deque(maxlen=MAX_LEDGER)
        self._failures = 0
        self._observed_step = 0
        self._counts = {
            "sessions_started": 0,
            "sessions_completed": 0,
            "insights_delivered": 0,
            "insights_dropped_stale": 0,
            "insights_dropped_late": 0,
            "insights_dropped_overflow": 0,
            "boundaries_superseded": 0,
            "degradations_recorded": 0,
        }
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
        with self._lock:
            if self._closed or self._degradation is not None:
                return
            if self._pending is not None:
                self._record(
                    DROPPED_BOUNDARY,
                    f"boundary {_kind_of(self._pending)!r} superseded before it was "
                    "thought about",
                    step_index=_step_of(self._pending),
                )
                self._counts["boundaries_superseded"] += 1
            self._pending = _carry(boundary)
            self._observed_step = max(self._observed_step, _step_of(boundary))
            self._idle.clear()
        if not self.start():
            # No thread means nothing will ever pick this up; say so rather than
            # leaving a boundary queued against a lane that does not exist.
            with self._lock:
                self._pending = None
                self._idle.set()
            return
        self._wake.set()

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
            for insight in ready:
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
        """Stop the lane: signal, bounded join, then account for what is left.

        Idempotent and safe to call from any thread but the worker's. Never
        hangs: the join is bounded, and a session parked inside an
        uninterruptible model call is simply left to the daemon thread. Insights
        still buffered — and any produced after this returns — have nowhere to
        go, so each is recorded as a late drop (C3) rather than vanishing.
        """
        with self._lock:
            first = not self._closed
            self._closed = True
            thread = self._thread
            stranded = list(self._ready)
            self._ready.clear()
            self._pending = None
        self._stop.set()
        self._wake.set()
        _bounded_join(thread, timeout=self._join_timeout if timeout is None else timeout)
        with self._lock:
            self._idle.set()
            if first:
                for insight in stranded:
                    self._drop_late(insight, "undrained when the runner closed")

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
                # The citation surface, reported so a host's own artifact can
                # show which remembered records its counsel was compiled from —
                # provenance a reader can check, not a claim they must trust.
                "compiled_from": list(self._compiled_from),
            }

    # ── the worker ───────────────────────────────────────────────────────────
    def _work(self) -> None:
        """The ONE thread's body: take a boundary, think about it, repeat.

        Exits on the stop signal only. Everything inside
        :meth:`~embodiment.muse.MuseLoop.think` is already guaranteed not to
        raise, so the outer guard here is for this module's own bugs: a worker
        that dies must leave a record, not an absent mind the host mistakes for
        a quiet one.
        """
        try:
            while not self._stop.is_set():
                boundary = self._take()
                if boundary is None:
                    # Poll-wake: sleep until woken, or until the poll bound, and
                    # re-check the stop flag either way.
                    self._wake.wait(self._poll_interval)
                    self._wake.clear()
                    continue
                self._absorb(self._loop.think(boundary, recall_bundle=self._recall_bundle))
        except Exception as exc:  # a dead worker is recorded, never silent
            with self._lock:
                self._degrade(DEGRADED_WORKER, f"{type(exc).__name__}: {exc}")
        finally:
            self._idle.set()

    def _take(self) -> Optional[BoundaryContext]:
        """Claim the queued boundary, or mark the lane idle. Worker thread only."""
        with self._lock:
            boundary = self._pending
            self._pending = None
            if boundary is None:
                self._idle.set()
                return None
            self._counts["sessions_started"] += 1
            return boundary

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
            if len(self._ready) == self._ready.maxlen:
                self._counts["insights_dropped_overflow"] += 1
                self._record(
                    DROPPED_OVERFLOW,
                    f"drain buffer full ({self._ready.maxlen}); the oldest insight was "
                    "discarded so the freshest thinking survives",
                    step_index=insight.origin.step_count,
                    model_turns=insight.turn_index,
                )
            self._ready.append(insight)

    def _absorb(self, outcome: MuseOutcome) -> None:
        """Fold one finished session's cost and degradations. Worker thread only."""
        with self._lock:
            self._counts["sessions_completed"] += 1
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
        if value <= 0 or value != value:  # non-positive or NaN
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
