"""The presence pump — ONE loop-internal engine every surface shares (task t7).

An interactive session, an attached talk lane, a background run's progress sink
and a mesh resident all drive the SAME beats — acknowledge, relay, proactive
update — through this engine, so "talking to one embodied agent" feels identical
on every front. It assumes **no TTY, no thread and no clock**: all IO rides the
injected :class:`PresenceIO` callbacks, and the update cadence is step/phase
based (:mod:`embodiment.presence`), never timer-based.

A REDESIGN, not a port
----------------------
This module is descended from colleague ``1.52.1``'s ``presence_engine.py``, but
it is **not** an extraction of it. Upstream, every boundary was handed to a
``SensesLoopDriver`` — the senses *coordination* loop, a second agentic loop that
answered each boundary with a tools-off JSON "move". embodiment ships exactly ONE
loop (the bounded tool loop; ``senses_loop.py`` stays in colleague), so the
driver seam had to be rebuilt around the pair embodiment actually ships:

* **cortex** — the worker that owns the bounded tool loop, performs repo actions
  and final synthesis, and holds **final authority**. The engine reaches it only
  through the acting callbacks on :class:`PresenceExecutor`
  (``dispatch_to_cortex`` / ``guide_cortex`` / ``read_flight``) and is called
  *by* it at each progress boundary (:class:`PresenceSink`).
* **muse** — an OPTIONAL advisory subconsciousness that comments on a boundary.
  It **proposes, never decides**: its narration is rendered to the operator and
  its guidance is appended to the running loop as plain advisory text. There is
  no path from a muse comment to a tool-call decision, because the acting
  surface has no deny/rewrite field to bind.

Why the muse seam is a DRAIN (task t10b, deviation d1)
------------------------------------------------------
t7's seam was a synchronous pull — one call per boundary, one comment back.
Deviation ``d1`` made the muse a *parallel* thinking loop running on its own
thread (:mod:`embodiment.muse_runner`), so an insight is almost never ready at
the boundary that prompted it. :class:`MuseSeam` is therefore two non-blocking
calls, plus a health probe:

* ``consider(boundary)`` — offer the boundary; the seam thinks about it wherever
  and whenever it likes. The pump does not wait, and gets nothing back.
* ``drain(step_count=…)`` — collect whatever finished in the meantime. **An
  empty drain is the normal case**, not a fault. ``step_count`` is the actor
  loop's current step, so a seam can judge its own insights' staleness.
* ``degradation()`` — ``None`` while the lane is healthy; a short reason once it
  has stopped thinking for good. A seam that reports one is unbound exactly like
  a seam that raises, with the same single operator-facing notice.

t7's callable is still accepted as :class:`MusePullSeam` and adapted internally,
so hosts (and :class:`embodiment.muse.MuseLoop`) that want one synchronous
thinking session per boundary keep working unchanged. **The thread lives in the
runner, never here**: this module still imports no ``threading``, no ``time``
and no ``asyncio``, and an AST test pins that.

Because a muse notice *comments on* a beat, a degradation is latched during the
beat and its notice is rendered **after** the beat's own turns land — never
ahead of the thing it is commenting on.

The **museless run is the default, primary path**, not a degraded exception: with
no muse configured the engine still acknowledges (from the intake packet's own
``ack``) and still narrates progress (from the loop's own state), spending zero
model calls. A muse that is configured and then fails degrades to that same
cortex-only lane — but *visibly*: the failed invocation, the transition, its
reason and a rendered notice all land where the host can see them.

Three lanes, one ladder — ``muse`` → ``cortex-only`` → ``off``. Only a
*transition* into ``cortex-only`` is a degradation; *starting* there is normal.

Why there is no ``import time`` here
------------------------------------
Upstream's docstring claimed "no TTY, no thread, no clock" while the module
imported ``time`` and stamped ``time.time()`` onto its capped-update record. The
contract loses either way: an engine that reads a clock is not clock-free, and a
record with no timestamp at all is less useful to a host folding it into an
artifact beside timestamped entries. This module resolves it by **injection**:
the optional ``clock`` constructor argument is the only source of a timestamp,
and its default (``None``) means the ``at`` key is **omitted entirely** rather
than fabricated as a zero. A fabricated timestamp would be exactly the silent
dishonesty constraint C3 forbids; an absent one is legible. An AST test pins
that ``time`` / ``threading`` / ``datetime`` / ``subprocess`` are never imported
here.

What this engine is NOT
-----------------------
It is not a presence *event stream*. There is no ``subscribe``, no ``emit``, no
listener registry: presence stays loop-internal, and expression surfaces
(``reterminal``, ``harmonics-cli``) are explicitly not consumers. :meth:`
PresenceEngine.snapshot` is a **pull-only** fold of what already happened,
shaped like the contract's ``SensesBlock`` fields so a host records it without
inventing a schema.

The engine imports NO front module (CLI / explain / ``__main__``) — fronts depend
on it, never the reverse, pinned by an import-graph test.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol, runtime_checkable

from embodiment.contract import SENSES_CHAT_KINDS, ContextPacket, SensesRecord
from embodiment.presence import UpdateCadence, should_update

__all__ = [
    "BOUNDARY_INTAKE",
    "BOUNDARY_OPERATOR_INPUT",
    "BOUNDARY_CADENCE_TICK",
    "DEFAULT_SPEAKER",
    "TURN_ACK",
    "TURN_UPDATE",
    "TURN_RELAY",
    "MODE_MUSE",
    "MODE_CORTEX_ONLY",
    "MODE_OFF",
    "SOURCE_PACKET",
    "SOURCE_OPERATOR",
    "SOURCE_MUSE",
    "SOURCE_CORTEX",
    "BoundaryContext",
    "MuseComment",
    "MusePullSeam",
    "MuseSeam",
    "PresenceEngine",
    "PresenceExecutor",
    "PresenceIO",
    "PresenceSink",
    "PresenceTurn",
    "build_presence_executor",
]

#: Default label the engine prefixes onto operator-facing lines. A host that has
#: resolved a teammate identity (see :mod:`embodiment.identity`) passes it as
#: ``speaker=``; absent one, this neutral default keeps output identical for
#: every unconfigured host — identity is configuration, never inference.
DEFAULT_SPEAKER = "presence"

#: The three boundaries a driving loop hands to the pump.
BOUNDARY_INTAKE = "intake"
BOUNDARY_OPERATOR_INPUT = "operator-input"
BOUNDARY_CADENCE_TICK = "cadence-tick"

#: The lanes of the degradation ladder. ``cortex-only`` is the DEFAULT lane, not
#: a fault state; only a *transition* into it (a muse that failed) is a
#: degradation, and that transition is always recorded.
MODE_MUSE = "muse"
MODE_CORTEX_ONLY = "cortex-only"
MODE_OFF = "off"

#: The contributing role a recorded line actually came from. A trace must never
#: let a single-model run look like two minds: a structural update is sourced to
#: ``cortex`` (it is derived from the loop's own state), and only text a muse
#: really produced is sourced to ``muse``.
SOURCE_PACKET = "packet"
SOURCE_OPERATOR = "operator"
SOURCE_MUSE = "muse"
SOURCE_CORTEX = "cortex"

#: Turn kinds. Chat-bearing turns reuse the contract's shared chat vocabulary;
#: an injection-bearing turn is a ``relay``.
TURN_ACK = "ack"
TURN_UPDATE = "update"
TURN_RELAY = "relay"

_CHAT_KIND_BY_BOUNDARY = {
    BOUNDARY_INTAKE: "talk",
    BOUNDARY_OPERATOR_INPUT: "talk",
    BOUNDARY_CADENCE_TICK: TURN_UPDATE,
}

_CAP_NOTICE = "(update cap reached — staying quiet now; EMBODIMENT_PRESENCE_UPDATE_CAP raises it)"
_MUSE_DEGRADED_NOTICE = "(muse unavailable — continuing cortex-only)"
_RELAY_PREFIX = "→ cortex: "
_MAX_DETAIL_LEN = 200


# ── the injected IO surface ───────────────────────────────────────────────────


def _noop_dispatch(_instruction: str) -> None:
    return None


def _noop_guidance(_text: str) -> None:
    return None


def _noop_read_flight() -> Any:
    return ""


def _noop_render(_line: str) -> None:
    return None


def _noop_poll() -> Optional[str]:
    return None


def _noop_feed_tail() -> Any:
    return ""


def _noop_task_state() -> Any:
    return None


def _noop_narrate(_line: str) -> None:
    return None


@dataclass
class PresenceIO:
    """The injected IO surface a host supplies — eight plain callables.

    No TTY, thread, socket or clock is implied by any of them, and every one is
    defaulted to a no-op, so a host wires only what it actually has and an
    unarmed surface stays a strict no-op.

    - ``dispatch_to_cortex(instruction)`` — hand a work instruction to the cortex
      loop. The engine passes the operator's **verbatim** words, never a model's
      reading of them. A host that starts the loop itself leaves this unset.
    - ``append_guidance(text)`` — append advisory text to the running loop (the
      one channel both an operator relay and a muse comment travel down).
    - ``read_flight()`` — the run's current status/feed, read once per *driven*
      boundary (never per step).
    - ``render(line)`` — display one presence line to the operator. A failure
      here is the host's to see: it is NOT swallowed.
    - ``poll_operator_input()`` — pending operator text (non-blocking), else
      ``None``.
    - ``feed_tail()`` — the recent feed tail used to ground a boundary.
    - ``task_state()`` — a short run snapshot (step / phase / last tool); the
      preferred source for a museless progress line.
    - ``narrate(line)`` — OPTIONAL text-to-speech narration of a rendered line.
      This is the ONE callback whose exceptions the engine swallows: a failed or
      absent synthesis must never disturb the text path it narrates. Everything
      else fails loudly, because a presence layer that hides its own breakage is
      worse than none.
    """

    dispatch_to_cortex: Callable[[str], Any] = _noop_dispatch
    append_guidance: Callable[[str], Any] = _noop_guidance
    read_flight: Callable[[], Any] = _noop_read_flight
    render: Callable[[str], None] = _noop_render
    poll_operator_input: Callable[[], Optional[str]] = _noop_poll
    feed_tail: Callable[[], Any] = _noop_feed_tail
    task_state: Callable[[], Any] = _noop_task_state
    narrate: Callable[[str], None] = _noop_narrate


@dataclass
class PresenceExecutor:
    """The ACTING surface: the three callbacks that touch the running loop.

    Separated from :class:`PresenceIO` (the *presence* surface: render, poll,
    narrate, and the two read-only state taps) so a host can wrap what the pump
    is allowed to DO — e.g. gate ``guide_cortex`` behind its own policy —
    without rebuilding its IO.

    There is deliberately no ``deny`` / ``rewrite`` / ``pre_tool`` field. The
    muse proposes and never decides, and that boundary is held by the
    *mechanism*: no muse-sourced value can reach a tool-call decision, because
    the engine has no callback through which it could.

    Upstream's executor also carried ``reply_to_operator`` and ``clarify``; both
    were no-ops there (the engine renders operator-facing text itself) and they
    named moves in a coordination-loop vocabulary that does not ship here, so
    they are dropped rather than kept as dead seams.
    """

    dispatch_to_cortex: Callable[[str], Any] = _noop_dispatch
    guide_cortex: Callable[[str], Any] = _noop_guidance
    read_flight: Callable[[], Any] = _noop_read_flight


def build_presence_executor(io: PresenceIO) -> PresenceExecutor:
    """Bind the acting callbacks to *io*.

    The engine builds one of these from its ``io`` when no executor is injected,
    so the common case stays a single wiring step.
    """
    return PresenceExecutor(
        dispatch_to_cortex=io.dispatch_to_cortex,
        guide_cortex=io.append_guidance,
        read_flight=io.read_flight,
    )


# ── the shapes the seams exchange ─────────────────────────────────────────────


@dataclass(frozen=True)
class BoundaryContext:
    """Everything the pump knows at one boundary — the muse's whole input.

    Frozen: an advisory seam reads the boundary, it never edits the run.
    """

    kind: str
    operator_input: Optional[str] = None
    packet: Optional[ContextPacket] = None
    feed_tail: Any = ""
    task_state: Any = None
    flight: Any = ""
    step_count: int = 0
    phase_changed: bool = False
    reason: str = ""
    history: Optional[list[dict[str, str]]] = None


@dataclass
class MuseComment:
    """What an advisory muse may return for one boundary — all of it optional.

    ``text`` is narration for the operator; ``guidance`` is advisory text
    appended to the running loop for the cortex to read and weigh. ``tokens``
    and ``latency`` are the seam's OWN measurements — the engine has no clock and
    never estimates them; absent, they stay ``None`` in the record rather than
    becoming a fabricated zero.
    """

    text: str = ""
    guidance: str = ""
    tokens: Optional[int] = None
    latency: Optional[float] = None


@dataclass
class PresenceTurn:
    """One beat's outcome: what was said, what was injected, and by whom.

    ``chat_entry`` / ``injection`` mirror the contract's ``SensesBlock`` entry
    shapes so a host folds them without a parallel schema; ``source`` names the
    role that actually contributed the text.
    """

    kind: str
    source: str
    chat_entry: Optional[dict[str, Any]] = None
    injection: Optional[dict[str, Any]] = None


@runtime_checkable
class MuseSeam(Protocol):
    """The optional advisory seam — a DRAIN, not a synchronous pull (task t10b).

    Three non-blocking calls. None of them may block the pump, because the pump
    runs inside the actor loop and the actor loop must never wait on a second
    mind:

    * ``consider(boundary)`` — offer one :class:`BoundaryContext` to think
      about. Returns nothing; whether, where and for how long the seam thinks is
      entirely its own business.
    * ``drain(step_count=…)`` — return whatever advisory comments are ready
      *now*, oldest first. **An empty list is the normal case.** ``step_count``
      is the actor loop's current step, handed over so a seam can judge whether
      its own insights have gone stale (see :func:`embodiment.muse.is_stale`).
    * ``degradation()`` — ``None`` while the lane is healthy; a short,
      operator-readable reason once it has stopped thinking for good. It is a
      METHOD rather than a property so this protocol stays callable-members-only
      and therefore usable with both ``isinstance`` and ``issubclass``.

    The seam receives no acting callbacks and returns no decision, only text.
    Any of the three may raise — the engine degrades and records rather than
    propagating — and it owns its own timing and configuration (an endpoint
    arrives through the host's explicit configuration when the seam is built,
    resolved BY ROLE NAME and never inferred from a model name by this module).
    """

    def consider(self, boundary: BoundaryContext) -> None: ...

    def drain(self, *, step_count: int = 0) -> list[MuseComment]: ...

    def degradation(self) -> Optional[str]: ...


@runtime_checkable
class MusePullSeam(Protocol):
    """t7's synchronous seam: one call per boundary, one comment back.

    Still accepted by :class:`PresenceEngine` and adapted onto
    :class:`MuseSeam` internally, so a host that wants one thinking session per
    boundary — on the caller's own thread, with no concurrency anywhere — keeps
    working unchanged. :class:`embodiment.muse.MuseLoop` satisfies exactly this,
    which is what let the pump move to the drain shape without a flag day.
    """

    def __call__(self, boundary: BoundaryContext) -> Optional[MuseComment]: ...


class _PullSeam:
    """Adapt a :class:`MusePullSeam` onto the drain shape.

    The pull happens inside ``consider`` — the same call, at the same boundary,
    with the same exceptions — and the comment is handed straight back by the
    ``drain`` that follows it in the same beat. So an adapted seam behaves
    exactly as it did under t7, and the engine has ONE code path.
    """

    def __init__(self, pull: Any) -> None:
        self._pull = pull
        self._ready: list[MuseComment] = []

    def consider(self, boundary: BoundaryContext) -> None:
        comment = self._pull(boundary)
        if comment is not None:
            self._ready.append(comment)

    def drain(self, *, step_count: int = 0) -> list[MuseComment]:
        ready, self._ready = self._ready, []
        return ready

    def degradation(self) -> Optional[str]:
        """Never self-reports: a pull seam signals failure by raising."""
        return None


def _as_drain_seam(muse: Any) -> Any:
    """Normalize either accepted muse shape to the drain shape. Never raises."""
    if muse is None:
        return None
    if hasattr(muse, "consider") and hasattr(muse, "drain"):
        return muse
    return _PullSeam(muse)


@runtime_checkable
class PresenceSink(Protocol):
    """What a driving loop calls — the contract task t4's bounded loop binds to.

    A loop takes an optional sink, calls :meth:`acknowledge` once before its
    first step, :meth:`on_progress_boundary` once per step, and routes any
    operator message through :meth:`on_operator_message`. With no sink (or an
    inactive one) the loop is byte-identical to a loop with no presence at all.
    """

    @property
    def active(self) -> bool: ...

    def acknowledge(self, packet: Optional[ContextPacket]) -> list[PresenceTurn]: ...

    def on_operator_message(self, text: str) -> list[PresenceTurn]: ...

    def on_progress_boundary(
        self, *, step_count: int = 0, phase_changed: bool = False
    ) -> list[PresenceTurn]: ...


# ── the pump ──────────────────────────────────────────────────────────────────


class PresenceEngine:
    """Drive the presence beats for one work item, front-agnostically.

    Args:
        io: the injected IO surface; a fully defaulted :class:`PresenceIO`
            (every callback a no-op) when omitted.
        executor: the acting surface; built from ``io`` when omitted.
        cadence: step/phase update cadence; :class:`UpdateCadence` defaults when
            omitted.
        muse: the OPTIONAL advisory seam — either the drain-shaped
            :class:`MuseSeam` (the primary shape) or t7's :class:`MusePullSeam`
            callable, which is adapted onto it. ``None`` — the default — is the
            primary tested path, not a degraded one, and costs no thread
            anywhere in the stack.
        history_provider: optional rolling-history callable threaded into every
            boundary. A provider that raises is recorded once and then left
            alone, never retried and never fatal.
        clock: the ONLY source of a wall-clock timestamp. ``None`` (the default)
            means recorded entries carry no ``at`` key at all.
        enabled: ``False`` disarms the lane entirely — every beat is a strict
            no-op and nothing is recorded.
        speaker: label prefixed onto operator-facing lines.
    """

    def __init__(
        self,
        *,
        io: Optional[PresenceIO] = None,
        executor: Optional[PresenceExecutor] = None,
        cadence: Optional[UpdateCadence] = None,
        muse: Optional[Any] = None,
        history_provider: Optional[Callable[[], Optional[list[dict[str, str]]]]] = None,
        clock: Optional[Callable[[], float]] = None,
        enabled: bool = True,
        speaker: str = DEFAULT_SPEAKER,
    ) -> None:
        self._io = io if io is not None else PresenceIO()
        self._executor = executor if executor is not None else build_presence_executor(self._io)
        self._cadence = cadence if cadence is not None else UpdateCadence()
        self._muse = _as_drain_seam(muse)
        self._history_provider = history_provider
        self._clock = clock
        self._enabled = bool(enabled)
        self._speaker = speaker or DEFAULT_SPEAKER

        self._packet: Optional[ContextPacket] = None
        # Cadence bookkeeping — step/phase based, never a clock.
        self._last_update_step = 0
        self._updates_sent = 0
        self._capped_recorded = False
        self._muse_degraded = False
        #: A latched muse degradation, rendered after the beat it commented on.
        self._pending_degradation: Optional[tuple[str, str]] = None
        # The ledger, shaped like the contract's SensesBlock fields.
        self._records: list[SensesRecord] = []
        self._chat: list[dict[str, Any]] = []
        self._injections: list[dict[str, Any]] = []

    # ── state ────────────────────────────────────────────────────────────────
    @property
    def active(self) -> bool:
        """True iff the presence lane is armed (not the ``off`` mode)."""
        return self._enabled

    @property
    def mode(self) -> str:
        """The current lane: ``off``, ``muse``, or ``cortex-only``."""
        if not self._enabled:
            return MODE_OFF
        return MODE_MUSE if self._muse is not None else MODE_CORTEX_ONLY

    @property
    def muse_degraded(self) -> bool:
        """True iff cortex-only was REACHED by a muse failure, not configured."""
        return self._muse_degraded

    @property
    def records(self) -> list[SensesRecord]:
        return list(self._records)

    # ── the beats ────────────────────────────────────────────────────────────
    def acknowledge(self, packet: Optional[ContextPacket]) -> list[PresenceTurn]:
        """The acknowledgment beat — runs before the loop's first step.

        Hands the operator's **verbatim** original text to the cortex loop (the
        verbatim invariant: never model output, never a normalized reading) and
        renders the packet's own ``ack`` if intake produced one. No model call
        happens here on the museless path.
        """
        self._packet = packet
        if not self.active or packet is None:
            return []
        original = getattr(packet, "original", "") or ""
        if original:
            self._executor.dispatch_to_cortex(original)

        turns: list[PresenceTurn] = []
        ack = (getattr(packet, "ack", None) or "").strip()
        if ack:
            turns.append(
                PresenceTurn(
                    kind=TURN_ACK,
                    source=SOURCE_PACKET,
                    chat_entry=self._stamp(
                        {"kind": TURN_ACK, "text": ack, "source": SOURCE_PACKET}
                    ),
                )
            )
        # Emit BEFORE consulting the muse: the operator hears the acknowledgment
        # first, so a muse remark — or a muse *degradation notice* — can never
        # land ahead of the beat it comments on.
        self._emit(turns)
        muse_turns = self._muse_turns(BOUNDARY_INTAKE)
        self._emit(muse_turns)
        self._flush_muse_degradation()
        return turns + muse_turns

    def on_operator_message(self, text: str) -> list[PresenceTurn]:
        """Relay an operator message into the running loop, verbatim."""
        if not self.active or not (text or "").strip():
            return []
        self._executor.guide_cortex(text)
        turns = [
            PresenceTurn(
                kind=TURN_RELAY,
                source=SOURCE_OPERATOR,
                injection=self._stamp({"text": text, "source": SOURCE_OPERATOR}),
            )
        ]
        self._emit(turns)
        muse_turns = self._muse_turns(BOUNDARY_OPERATOR_INPUT, operator_input=text)
        self._emit(muse_turns)
        self._flush_muse_degradation()
        return turns + muse_turns

    def on_progress_boundary(
        self, *, step_count: int = 0, phase_changed: bool = False
    ) -> list[PresenceTurn]:
        """Process one loop progress boundary: poll input, else cadence-gate.

        Operator input wins (a live message is answered immediately); otherwise a
        proactive update fires only when the step/phase cadence says so, bounded
        by the per-run cap. Reaching the cap is recorded ONCE and never silently.
        """
        if not self.active:
            return []
        pending = self._io.poll_operator_input()
        if pending is not None and pending.strip():
            return self.on_operator_message(pending)

        fire, reason = should_update(
            self._cadence,
            step_count=step_count,
            last_update_step=self._last_update_step,
            phase_changed=phase_changed,
            updates_sent=self._updates_sent,
        )
        if reason == "cap":
            self._record_cap()
            return []
        if not fire:
            return []
        # A fired attempt consumes budget whether or not it produces text —
        # honest cadence accounting beats a silent retry loop.
        self._updates_sent += 1
        self._last_update_step = step_count
        return self._update_turns(step_count, phase_changed, reason)

    # ── artifact ─────────────────────────────────────────────────────────────
    def snapshot(self) -> dict[str, Any]:
        """A PULL-ONLY fold of what already happened — never a live stream.

        Shaped like the contract's ``SensesBlock`` fields (``records`` / ``chat``
        / ``injections``) so a host records presence without inventing a schema.
        Returns copies: a caller cannot mutate the engine's ledger through it.
        """
        return {
            "records": list(self._records),
            "chat": [dict(entry) for entry in self._chat],
            "injections": [dict(entry) for entry in self._injections],
        }

    # ── internals ────────────────────────────────────────────────────────────
    def _update_turns(
        self, step_count: int, phase_changed: bool, reason: str
    ) -> list[PresenceTurn]:
        """Produce the fired update's turns: the muse's line, else a structural one."""
        boundary = self._boundary(
            BOUNDARY_CADENCE_TICK,
            step_count=step_count,
            phase_changed=phase_changed,
            reason=reason,
        )
        turns = self._muse_turns_for(boundary)
        if not any(turn.chat_entry is not None for turn in turns):
            text = _structural_update(boundary)
            if text:
                turns.insert(
                    0,
                    PresenceTurn(
                        kind=TURN_UPDATE,
                        source=SOURCE_CORTEX,
                        chat_entry=self._stamp(
                            {"kind": TURN_UPDATE, "text": text, "source": SOURCE_CORTEX}
                        ),
                    ),
                )
        self._emit(turns)
        self._flush_muse_degradation()
        return turns

    def _record_cap(self) -> None:
        """Record the update cap exactly once — reached is never silent."""
        if self._capped_recorded:
            return
        self._capped_recorded = True
        self._chat.append(self._stamp({"kind": TURN_UPDATE, "capped": True}))
        self._io.render(f"{self._speaker}: {_CAP_NOTICE}")

    def _boundary(
        self,
        kind: str,
        *,
        operator_input: Optional[str] = None,
        step_count: int = 0,
        phase_changed: bool = False,
        reason: str = "",
    ) -> BoundaryContext:
        """Build the boundary. Only ever called when a turn is about to be driven."""
        return BoundaryContext(
            kind=kind,
            operator_input=operator_input,
            packet=self._packet,
            feed_tail=self._io.feed_tail(),
            task_state=self._io.task_state(),
            flight=self._executor.read_flight(),
            step_count=step_count,
            phase_changed=phase_changed,
            reason=reason,
            history=self._history() if self._muse is not None else None,
        )

    def _muse_turns(self, kind: str, *, operator_input: Optional[str] = None) -> list[PresenceTurn]:
        """Ask the muse about a boundary it is the only reason to build."""
        if self._muse is None:
            return []
        return self._muse_turns_for(self._boundary(kind, operator_input=operator_input))

    def _muse_turns_for(self, boundary: BoundaryContext) -> list[PresenceTurn]:
        """Offer the boundary to the seam, collect what is ready, and record it.

        Two non-blocking calls and a health probe — never a wait. A drain that
        comes back empty is the NORMAL case under deviation d1 (the muse is
        still thinking, elsewhere), so it is recorded as a completed, healthy
        invocation, exactly as a silent pull seam was under t7.
        """
        muse = self._muse
        if muse is None:
            return []
        try:
            muse.consider(boundary)
            drained = muse.drain(step_count=boundary.step_count)
            reason = muse.degradation()
        except Exception as exc:  # noqa: BLE001 - a failing muse degrades, never aborts
            self._latch_degradation(boundary.kind, exc)
            return []
        comments = list(drained) if isinstance(drained, (list, tuple)) else []
        self._record_drain(boundary.kind, comments)
        if reason:
            # Its last words still land this beat; the notice follows them.
            self._latch_degradation(boundary.kind, reason)
        turns: list[PresenceTurn] = []
        for comment in comments:
            turns.extend(self._comment_turns(boundary, comment))
        return turns

    def _record_drain(self, kind: str, comments: list[MuseComment]) -> None:
        """One record per drained comment; one for a drain that came back empty."""
        point = f"muse:{kind}"
        if not comments:
            self._records.append(SensesRecord(point=point, degraded=False))
            return
        for comment in comments:
            self._records.append(
                SensesRecord(
                    point=point,
                    latency=getattr(comment, "latency", None),
                    tokens=getattr(comment, "tokens", None),
                    degraded=False,
                )
            )

    def _comment_turns(self, boundary: BoundaryContext, comment: MuseComment) -> list[PresenceTurn]:
        """Turn one muse comment into turns — narration, then advisory guidance."""
        turns: list[PresenceTurn] = []
        chat_kind = _chat_kind(boundary.kind)
        text = (getattr(comment, "text", "") or "").strip()
        if text:
            turns.append(
                PresenceTurn(
                    kind=chat_kind,
                    source=SOURCE_MUSE,
                    chat_entry=self._stamp(
                        {"kind": chat_kind, "text": text, "source": SOURCE_MUSE}
                    ),
                )
            )
        guidance = (getattr(comment, "guidance", "") or "").strip()
        if guidance:
            # The advisory channel, and the only one: plain text the cortex reads
            # and weighs. Applied BEFORE it is recorded, so a recorded injection
            # always means an applied one.
            self._executor.guide_cortex(guidance)
            turns.append(
                PresenceTurn(
                    kind=TURN_RELAY,
                    source=SOURCE_MUSE,
                    injection=self._stamp({"text": guidance, "source": SOURCE_MUSE}),
                )
            )
        return turns

    def _latch_degradation(self, kind: str, detail: Any) -> None:
        """Unbind the seam NOW; render the notice after the beat lands.

        Unbinding is immediate so a dead endpoint is never re-dialled — not at
        the next step and not later in this beat. The operator-facing half is
        deferred to :meth:`_flush_muse_degradation` because a muse notice
        *comments on* a beat and must not arrive ahead of it. The first
        degradation wins: a seam only dies once.
        """
        self._muse = None
        if self._pending_degradation is None:
            self._pending_degradation = (kind, str(detail)[:_MAX_DETAIL_LEN])

    def _flush_muse_degradation(self) -> None:
        """Drop to the cortex-only lane, visibly (C3) and exactly once.

        Records the failed invocation, the transition itself and the reason,
        then renders one notice. Presence continues on the structural lane.
        """
        pending = self._pending_degradation
        if pending is None:
            return
        self._pending_degradation = None
        kind, detail = pending
        self._records.append(SensesRecord(point=f"muse:{kind}", degraded=True))
        self._records.append(SensesRecord(point="muse:degraded-off", degraded=True))
        self._muse_degraded = True
        self._chat.append(
            self._stamp({"kind": TURN_UPDATE, "degraded": SOURCE_MUSE, "detail": detail})
        )
        self._io.render(f"{self._speaker}: {_MUSE_DEGRADED_NOTICE}")

    def _history(self) -> Optional[list[dict[str, str]]]:
        """Read the optional history provider; a failure is recorded, then dropped."""
        provider = self._history_provider
        if provider is None:
            return None
        try:
            return provider()
        except Exception:  # noqa: BLE001 - recorded below; never fatal, never retried
            self._history_provider = None
            self._records.append(SensesRecord(point="history", degraded=True))
            return None

    def _stamp(self, entry: dict[str, Any]) -> dict[str, Any]:
        """Add ``at`` only when a clock was injected — never a fabricated zero."""
        if self._clock is not None:
            entry["at"] = self._clock()
        return entry

    def _emit(self, turns: list[PresenceTurn]) -> None:
        """Record each turn into the ledger, then render it."""
        for turn in turns:
            if turn.chat_entry is not None:
                self._chat.append(turn.chat_entry)
            if turn.injection is not None:
                self._injections.append(turn.injection)
            self._render_turn(turn)

    def _render_turn(self, turn: PresenceTurn) -> None:
        """Display one turn's operator-facing text (side effects already applied)."""
        entry = turn.chat_entry
        if entry is not None:
            text = str(entry.get("text") or "").strip()
            if text:
                self._io.render(f"{self._speaker}: {text}")
                self._narrate(text)
        if turn.injection is not None:
            relay = str(turn.injection.get("text") or "").strip()
            if relay:
                # Relays are shown, not spoken: the voice says what the operator
                # is told, never what is whispered into the loop.
                self._io.render(f"{_RELAY_PREFIX}{relay}")

    def _narrate(self, text: str) -> None:
        """Best-effort narration — the ONE callback whose exceptions are swallowed.

        Runs strictly after ``render`` and is deliberately over-defensive: a
        failed or absent synthesis can never alter the text path it narrates, so
        an unwired or broken voice hook leaves every rendered line identical.
        """
        try:
            self._io.narrate(text)
        except Exception:  # nosec B110 # noqa: BLE001 - voice must never disturb text
            pass


# ── helpers ───────────────────────────────────────────────────────────────────


def _chat_kind(boundary_kind: str) -> str:
    """The shared-vocabulary chat kind for a boundary (never a new schema)."""
    kind = _CHAT_KIND_BY_BOUNDARY.get(boundary_kind, "talk")
    return kind if kind in SENSES_CHAT_KINDS else "talk"


def _last_line(value: Any) -> str:
    """The last non-empty line of a state/feed value, or ``""``."""
    text = str(value or "").strip()
    if not text:
        return ""
    return text.splitlines()[-1].strip()


def _structural_update(boundary: BoundaryContext) -> str:
    """A progress line built from the loop's OWN state — no model, no invention.

    This is what keeps the museless run genuinely present: the text is derived
    from what the run actually reports (state, then flight, then feed tail), so
    it can be stale but never fabricated. Nothing to report means nothing is
    said.
    """
    for value in (boundary.task_state, boundary.flight, boundary.feed_tail):
        line = _last_line(value)
        if line:
            return f"still working — {line}"
    return ""
