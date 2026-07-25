"""The muse's thinking loop — bounded, iterative, tools-off, advisory (task t10a).

embodiment now runs **two** loops. :mod:`embodiment.loop` is the *actor* loop:
think-and-act with tools, final authority, one summary. This module is the
*thinking* loop: the muse reasons in short iterative turns about where the actor
loop currently stands, and produces advisory text. It acts on nothing.

Why a loop and not a single completion (deviation d1)
-----------------------------------------------------
Task t7 gave the pump a synchronous advisory seam: one
:class:`~embodiment.presence_engine.MuseComment` per boundary, one completion.
Deviation ``d1`` replaced it. A 31B thinking model does not reason usefully in a
single completion — it needs turns. So the muse becomes a bounded loop of its
own, and (in task t10b) that loop runs on a thread beside the actor loop rather
than blocking it.

Everything about *concurrency* is t10b's. This module contains no thread, no
clock, no timer and no event: it is pure, deterministic and exhaustively
testable with nothing running in parallel. That split is deliberate — all the
reasoning logic is provable before a thread is anywhere near it.

Termination is an honesty condition
-----------------------------------
A background loop that thinks forever is both a resource leak and a lie.
:func:`_think_loop` has **exactly four** exits and they are the only ones:

* :data:`MUSE_EXIT_CONCLUDED` — the muse wrote :data:`MARKER_DONE`;
* :data:`MUSE_EXIT_QUIET` — it produced nothing usable for
  ``max_quiet_turns`` consecutive turns (a thinking lane that has run dry);
* :data:`MUSE_EXIT_BUDGET` — ``max_turns`` thinking turns were spent;
* :data:`MUSE_EXIT_DEGRADED` — the injected ``complete`` failed; the failure is
  recorded and the session stops cleanly (constraint C3).

``_think_loop`` contains no ``raise``, no ``try`` and no ``for``; its single
``while`` is bounded by a turn counter that every iteration increments (the one
path that does not increment it returns immediately). ``tests/test_muse.py``
proves all of that by AST, exactly as ``tests/test_loop.py`` does for the actor
loop.

Tools-off, and structurally so
------------------------------
The muse's whole seam is :data:`MuseCompleteFn` — *messages in, one model
response out*. There is no executor parameter, no tool schema, and nothing here
ever reads a response's tool-call list: a response that carries tool calls
simply has them ignored, because no code in this module looks at them. The
module never imports :mod:`embodiment.loop`, so no hook or decision type is even
in scope. ``tests/test_muse.py`` scans this file for the whole tool vocabulary.

Advisory only — proposes, never decides
---------------------------------------
This is the public promise on `colleague#352
<https://github.com/agentculture/colleague/issues/352>`_, and it is held by the
*mechanism*. A :class:`MuseInsight` extends the pump's own
:class:`~embodiment.presence_engine.MuseComment` and carries **only** text plus
provenance (``text``, ``guidance``, ``tokens``, ``latency``, ``origin``,
``turn_index``). There is no ``decision``, ``deny``, ``rewrite`` or ``arguments``
field for a tool-approval path to bind to, and :data:`MUSE_AUTHORITY` — the
authority boundary — is prepended to the system message of **every** thinking
turn. Host framing is *appended* to it, so no configuration can drop it.

Insights carry the boundary they reasoned about
-----------------------------------------------
This is new under d1 and it is load-bearing. In the synchronous design an
insight was always about the boundary that requested it. In a parallel loop, an
insight computed against step 3 can arrive at step 40. So every insight carries
a :class:`MuseOrigin` — the boundary ``kind``, its ``step_count``, its ``reason``
and the muse's own session counter — captured **when the session started**, not
when the insight is read. :func:`insight_lag` and :func:`is_stale` turn that
stamp into a relevance judgement; the threshold is the consumer's policy, and
:data:`DEFAULT_STALE_LAG` is only a default.

Reporting cost without a clock
------------------------------
:class:`~embodiment.presence_engine.MuseComment` records ``tokens`` and
``latency`` as the seam's OWN measurements, because the pump has no clock and
never estimates. Neither does this loop. So:

* **Turns are the loop's native cost unit.** :attr:`MuseOutcome.turns` is the
  number of model turns actually spent — a step-based measure, exactly as the
  presence cadence is step-based rather than timer-based. It is always honest
  and never needs a clock.
* **Tokens are reported, never estimated.** They are summed from what the seam
  itself returns. A response reporting ``0`` prompt *and* ``0`` completion tokens
  is treated as **unreported** (``None``), not as a real zero: a completion
  always has a prompt, so ``0/0`` can only mean "this seam does not report
  usage". ``None`` is legible; a fabricated ``0`` is not.
* **Latency is only ever a measurement.** With no ``clock`` injected it stays
  ``None`` on every insight and on the outcome — never ``0.0``. A host that owns
  a clock (t10b owns a thread, so it may) injects one and gets real numbers; a
  clock that itself raises degrades the *measurement* only, never the thinking.
  This mirrors :class:`~embodiment.presence_engine.PresenceEngine`'s injected
  ``clock`` and :func:`embodiment.perception.perceive`'s.

The protocol task t10b must conform to
--------------------------------------
t10b builds the ``ThreadedMuseRunner`` and reshapes the pump's seam into a drain.
This module is built for exactly that:

1. **Construct once, call per boundary.** :meth:`MuseLoop.think` is re-entrant
   in the sense that matters — it holds no state between sessions except the
   monotonic session counter. Run ONE session at a time on ONE thread; the
   counter is not synchronised, and two concurrent sessions on one instance
   would interleave it. One runner, one loop instance, one in-flight session.
2. **Drain through ``sink``, not by polling.** The optional ``sink`` is called
   with each :class:`MuseInsight` **as it is produced**, so a runner hands it a
   queue's ``put_nowait`` and gets incremental delivery. A sink that raises is
   recorded once, disabled, and never retried — it can never abort thinking.
   The full list is still on :attr:`MuseOutcome.insights`, so a drop is
   diagnosable.
3. **Judge relevance with :func:`insight_lag` / :func:`is_stale`**, against the
   actor loop's *current* step. Do not assume an insight is about now.
4. **Never raise into the actor loop.** :meth:`MuseLoop.think` degrades on every
   ``Exception``; only ``BaseException`` (a Ctrl-C) passes through.
5. **The pump's existing seam still works.** :meth:`MuseLoop.__call__` folds one
   whole session into a single ``MuseComment``, so ``MuseLoop`` satisfies today's
   ``MuseSeam`` unchanged. t10b can move to ``think``/drain without a flag day.
6. **Keep the import direction.** ``muse`` imports ``presence_engine``; the
   reverse would cycle. A runner that needs both belongs in its own module.

Stdlib only (constraint C1): ``dataclasses``, ``re``, ``typing``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from embodiment.contract import ModelResponse
from embodiment.presence_engine import BoundaryContext, MuseComment

__all__ = [
    # exits
    "MUSE_EXIT_CONCLUDED",
    "MUSE_EXIT_QUIET",
    "MUSE_EXIT_BUDGET",
    "MUSE_EXIT_DEGRADED",
    "MUSE_EXIT_REASONS",
    # degradation vocabulary (C3)
    "DEGRADED_THINKING",
    "DEGRADED_SINK",
    "DEGRADED_UNREADABLE",
    # protocol
    "MUSE_AUTHORITY",
    "MARKER_DONE",
    "MARKER_GUIDANCE",
    # shapes
    "MuseControls",
    "MuseDegradation",
    "MuseInsight",
    "MuseOrigin",
    "MuseOutcome",
    "MuseCompleteFn",
    "MuseSink",
    # driving
    "MuseLoop",
    # staleness
    "DEFAULT_STALE_LAG",
    "insight_lag",
    "is_stale",
]


# ── exits ─────────────────────────────────────────────────────────────────────

#: The muse said it had nothing further worth saying (:data:`MARKER_DONE`).
MUSE_EXIT_CONCLUDED = "concluded"
#: Consecutive turns produced nothing usable — the lane has run dry.
MUSE_EXIT_QUIET = "quiet"
#: ``max_turns`` thinking turns were spent without concluding.
MUSE_EXIT_BUDGET = "budget"
#: The injected thinking seam failed; recorded, then a clean stop.
MUSE_EXIT_DEGRADED = "degraded"
#: The complete set. There is no fifth; see the module docstring.
MUSE_EXIT_REASONS = (
    MUSE_EXIT_CONCLUDED,
    MUSE_EXIT_QUIET,
    MUSE_EXIT_BUDGET,
    MUSE_EXIT_DEGRADED,
)


# ── degradation vocabulary (C3) ───────────────────────────────────────────────

#: The injected ``complete`` raised, returned nothing, or returned something
#: whose content could not be read. All four fault classes the build brief names
#: (dead port, request error, overflow, lossy payload) land here identically.
DEGRADED_THINKING = "muse-thinking-failed"
#: The injected insight sink raised; it is disabled for the rest of the session.
DEGRADED_SINK = "muse-sink-failed"
#: A boundary field could not be rendered into the prompt; the field is NAMED.
DEGRADED_UNREADABLE = "muse-context-unreadable"


# ── the thinking protocol ─────────────────────────────────────────────────────

#: The authority boundary, prepended to the system message of EVERY thinking
#: turn (colleague#352). Deliberately identity-neutral: it names no teammate, no
#: model and no vendor, so an unconfigured muse never implies a second mind.
#: Identity framing is task t12's, and arrives through ``system``.
MUSE_AUTHORITY = (
    "You are an advisory thinking lane running beside an acting loop you do not "
    "control. You have no tools, no shell and no access to any repository or "
    "file. Nothing you write is executed, and you cannot approve, deny or "
    "rewrite anything the acting loop does. You propose; the acting loop decides "
    "and holds final authority over every action.\n"
    "Think in short iterative turns. Keep each turn to a few sentences. Prefix "
    "any line meant for the acting loop with 'GUIDANCE:'; everything else is "
    "narration for the operator. Write '[done]' when you have nothing further "
    "worth saying."
)

#: Written by the muse to end its own session.
MARKER_DONE = "[done]"
#: Line prefix marking advisory text meant for the acting loop.
MARKER_GUIDANCE = "GUIDANCE:"

#: Appended after each turn so the next one is a genuine continuation.
_CONTINUE = "Continue thinking, or write [done] if you have nothing further worth saying."

_DONE_RE = re.compile(re.escape(MARKER_DONE), re.IGNORECASE)
_GUIDANCE_RE = re.compile(r"^\s*guidance\s*:\s*", re.IGNORECASE)

#: Cap on a recorded degradation's reason text, so a runaway traceback from a
#: misbehaving seam cannot blow up a host's artifact. Mirrors continuity.py.
_MAX_REASON_LEN = 500
#: How many trailing history entries are rendered into the opening prompt.
_MAX_HISTORY_ENTRIES = 6
#: Cap on a rendered history entry's role label.
_MAX_ROLE_LEN = 32

#: The boundary fields rendered into the opening prompt, in order, as
#: ``(label, attribute)``. A field that is absent or empty is simply omitted.
_CONTEXT_FIELDS = (
    ("boundary", "kind"),
    ("step", "step_count"),
    ("why", "reason"),
    ("operator just said", "operator_input"),
    ("run state", "task_state"),
    ("flight", "flight"),
    ("feed tail", "feed_tail"),
)

_OPENING = "Consider where the acting loop currently stands."


# ── shapes ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MuseOrigin:
    """WHERE and WHEN an insight was reasoned about — the staleness key.

    Captured once, when a session starts, from the boundary handed to
    :meth:`MuseLoop.think`. Because the muse's loop runs in parallel with the
    actor loop (deviation d1), an insight can be *read* long after the position
    it was reasoning about; this is what lets a consumer notice.

    Fields
    ------
    kind:
        The boundary kind (``intake`` / ``operator-input`` / ``cadence-tick``).
    step_count:
        The acting loop's step at the moment the session started. Compare it
        against the acting loop's *current* step — see :func:`insight_lag`.
    session:
        The muse's own monotonic session number, so insights from different
        thinking sessions can be ordered even when their steps tie.
    reason:
        The cadence reason the boundary carried, when it had one.
    """

    kind: str = ""
    step_count: int = 0
    session: int = 0
    reason: str = ""

    @classmethod
    def of(cls, boundary: Optional[BoundaryContext], *, session: int = 0) -> "MuseOrigin":
        """Stamp *boundary*. Never raises; a missing boundary yields empty fields."""
        return cls(
            kind=_plain(_attr(boundary, "kind")),
            step_count=_coerce_int(_attr(boundary, "step_count")),
            session=_coerce_int(session),
            reason=_plain(_attr(boundary, "reason")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "step_count": self.step_count,
            "session": self.session,
            "reason": self.reason,
        }


@dataclass
class MuseInsight(MuseComment):
    """One advisory thought, stamped with the boundary it reasoned about.

    Extends the pump's :class:`~embodiment.presence_engine.MuseComment` rather
    than paralleling it, so an insight *is* a comment everywhere the engine
    already accepts one, and adds only provenance. Every field is text or a
    number: there is nothing here a tool-approval decision could bind to.

    ``latency`` is inherited and stays ``None`` unless a clock was injected —
    see the module docstring on reporting cost without one.
    """

    origin: MuseOrigin = field(default_factory=MuseOrigin)
    turn_index: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "guidance": self.guidance,
            "tokens": self.tokens,
            "latency": self.latency,
            "turn_index": self.turn_index,
            "origin": self.origin.to_dict(),
        }


@dataclass(frozen=True)
class MuseDegradation:
    """One recorded, host-visible thinking degradation (constraint C3).

    Deliberately field-for-field identical to
    :class:`embodiment.loop.LoopDegradation` — including ``to_dict`` — so the
    degradation ledger (task t9) folds ONE shape rather than two. The mapping:
    ``step_index`` is the *acting* loop's step this session was reasoning about
    (the origin's), and ``model_turns`` is how many thinking turns had been
    spent when it degraded.

    It is not imported from :mod:`embodiment.loop` on purpose: the muse does not
    consume the actor loop, and importing it would drag 1600 lines (and a
    decision vocabulary this module must not have in scope) into a lane that
    needs neither. :mod:`embodiment.continuity` sets the same precedent.
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


@dataclass(frozen=True)
class MuseControls:
    """The thinking loop's knobs. Every default is deliberately small.

    Fields
    ------
    max_turns:
        The session's model-turn budget — the whole termination guarantee. A
        value below ``1`` still buys one turn (a zero-turn "thought" would be a
        silently empty lane); nothing anywhere adds to it.
    max_quiet_turns:
        How many CONSECUTIVE turns may produce nothing usable before the session
        exits :data:`MUSE_EXIT_QUIET`. Cheaper than burning the whole budget on
        an empty stream.
    max_context_chars:
        Per-field cap when rendering the boundary into the opening prompt.
        ``0`` disables the cap.
    max_insight_chars:
        Cap on one insight's narration and on its guidance. A parallel advisory
        lane must not be able to flood the acting loop's guidance channel.
        ``0`` disables the cap.
    """

    max_turns: int = 4
    max_quiet_turns: int = 1
    max_context_chars: int = 600
    max_insight_chars: int = 2000


@dataclass(frozen=True)
class MuseOutcome:
    """What one thinking session produced, and what it cost.

    ``turns`` is the loop's own honest cost unit; ``tokens`` is what the seam
    reported (``None`` when it reported nothing); ``latency`` is a measurement
    only, present solely when a clock was injected.
    """

    origin: MuseOrigin
    exit_reason: str
    insights: list[MuseInsight] = field(default_factory=list)
    turns: int = 0
    tokens: Optional[int] = None
    latency: Optional[float] = None
    degradations: list[MuseDegradation] = field(default_factory=list)

    @property
    def degraded(self) -> bool:
        """True iff anything in this session degraded — never inferred elsewhere."""
        return bool(self.degradations)

    def comment(self) -> Optional[MuseComment]:
        """Fold the whole session into ONE comment for a synchronous consumer.

        ``None`` when the session produced no usable text at all (including
        every degraded session) — which the pump reads as the muse staying
        silent, exactly as it reads a seam returning ``None`` today.
        """
        text = "\n".join(i.text for i in self.insights if i.text).strip()
        guidance = "\n".join(i.guidance for i in self.insights if i.guidance).strip()
        if not text and not guidance:
            return None
        return MuseComment(text=text, guidance=guidance, tokens=self.tokens, latency=self.latency)

    def to_dict(self) -> dict[str, Any]:
        return {
            "origin": self.origin.to_dict(),
            "exit_reason": self.exit_reason,
            "insights": [i.to_dict() for i in self.insights],
            "turns": self.turns,
            "tokens": self.tokens,
            "latency": self.latency,
            "degradations": [d.to_dict() for d in self.degradations],
        }


#: The muse's ENTIRE seam: one tools-off model turn, messages in, response out.
#: No tool schema is ever passed and no tool result is ever read — that is the
#: whole of "tools-off", and it is enforced by there being nothing else here.
MuseCompleteFn = Callable[[list[dict[str, Any]]], ModelResponse]

#: An optional drain, called with each insight AS IT IS PRODUCED. Task t10b
#: hands it a queue. Never control-bearing: a raise disables it and is recorded.
MuseSink = Callable[[MuseInsight], None]


# ── the session context ───────────────────────────────────────────────────────


@dataclass
class _Session:
    """The collaborators threaded through one thinking session's helpers."""

    complete: MuseCompleteFn
    controls: MuseControls
    origin: MuseOrigin
    messages: list[dict[str, Any]]
    sink: Optional[MuseSink] = None
    clock: Optional[Callable[[], float]] = None
    insights: list[MuseInsight] = field(default_factory=list)
    degradations: list[MuseDegradation] = field(default_factory=list)
    turns: int = 0
    tokens: Optional[int] = None
    last_tokens: Optional[int] = None
    turn_started: Optional[float] = None
    sink_failed: bool = False


def _degrade(ctx: _Session, code: str, reason: str) -> None:
    """Record one host-visible degradation. Nothing here degrades silently."""
    ctx.degradations.append(
        MuseDegradation(
            code=code,
            reason=str(reason)[:_MAX_REASON_LEN],
            step_index=ctx.origin.step_count,
            model_turns=ctx.turns,
        )
    )


def _emit(ctx: _Session, insight: MuseInsight) -> None:
    """Offer one insight to the drain, if there is one.

    A raising sink is recorded ONCE and then disabled for the session: retrying
    a closed queue would spam the ledger, and dropping it silently would hide a
    real breakage. The insight still lands on the outcome either way.
    """
    sink = ctx.sink
    if sink is None or ctx.sink_failed:
        return
    try:
        sink(insight)
    except Exception as exc:  # noqa: BLE001 - a drain never controls the thinking
        ctx.sink_failed = True
        _degrade(ctx, DEGRADED_SINK, f"{exc}")


def _complete_turn(ctx: _Session) -> Optional[str]:
    """Run ONE tools-off model turn; return its content, or ``None`` if it failed.

    The turn is counted before the call, so a degraded attempt is accounted
    honestly rather than vanishing. Reading the response happens inside the same
    ``try``: a dead port, a request error, an overflow, a ``None`` reply and a
    response whose ``content`` explodes are all one fault class, all recorded
    identically, and none of them reaches the caller as an exception.
    """
    ctx.turns += 1
    ctx.turn_started = _now(ctx.clock)
    try:
        response = ctx.complete(list(ctx.messages))
        if response is None:
            raise ValueError("the thinking seam returned no response")
        content = _content(response)
        tokens = _token_total(response)
    except Exception as exc:  # noqa: BLE001 - every fault degrades identically (C3)
        _degrade(ctx, DEGRADED_THINKING, f"{exc}")
        return None
    ctx.last_tokens = tokens
    ctx.tokens = _add_tokens(ctx.tokens, tokens)
    return content


def _advance_turn(ctx: _Session, content: str, quiet: int) -> tuple[int, Optional[str]]:
    """Fold one turn's content into an insight; return ``(quiet, exit_or_None)``.

    Raises nothing: every step is string handling, list appends, or the guarded
    drain. The done marker is checked BEFORE the quiet counter is consulted, so
    a muse whose final turn is a bare ``[done]`` concludes rather than reading as
    having run dry.
    """
    done = bool(_DONE_RE.search(content))
    text, guidance = _split_content(content, ctx.controls.max_insight_chars)
    if text or guidance:
        insight = MuseInsight(
            text=text,
            guidance=guidance,
            tokens=ctx.last_tokens,
            latency=_since(ctx.clock, ctx.turn_started),
            origin=ctx.origin,
            turn_index=ctx.turns,
        )
        ctx.insights.append(insight)
        _emit(ctx, insight)
        quiet = 0
    else:
        quiet += 1

    # Keep the session genuinely iterative: the muse reads its own prior turn.
    if content.strip():
        ctx.messages.append({"role": "assistant", "content": content})
    ctx.messages.append({"role": "user", "content": _CONTINUE})

    if done:
        return quiet, MUSE_EXIT_CONCLUDED
    if quiet >= max(1, ctx.controls.max_quiet_turns):
        return quiet, MUSE_EXIT_QUIET
    return quiet, None


def _think_loop(ctx: _Session, max_turns: int) -> str:
    """Run the bounded thinking loop; return one of the four ``MUSE_EXIT_*``.

    Termination, in full:

    * ``budget`` is a fixed positive integer computed before the loop and never
      written to again — nothing in this module can extend it;
    * every iteration begins with :func:`_complete_turn`, whose FIRST statement
      increments ``ctx.turns``, unconditionally;
    * ``ctx.turns`` is decremented nowhere in this module;
    * so every iteration that continues has strictly increased the loop variable
      toward its fixed bound, and the ``while`` runs at most ``budget`` times.

    There are exactly four ``return`` statements, no ``raise``, no ``try`` and no
    second loop. Whatever the injected seam raises is caught and recorded one
    frame down, so a fifth way out does not exist even in principle.
    ``tests/test_muse.py`` asserts each of those properties by AST.
    """
    quiet = 0
    budget = max(1, _coerce_int(max_turns, 1))
    while ctx.turns < budget:
        content = _complete_turn(ctx)
        if content is None:
            return MUSE_EXIT_DEGRADED
        quiet, exit_reason = _advance_turn(ctx, content, quiet)
        if exit_reason == MUSE_EXIT_CONCLUDED:
            return MUSE_EXIT_CONCLUDED
        if exit_reason == MUSE_EXIT_QUIET:
            return MUSE_EXIT_QUIET
    return MUSE_EXIT_BUDGET


# ── the public loop object ────────────────────────────────────────────────────


class MuseLoop:
    """The muse's bounded thinking loop — construct once, think per boundary.

    Args:
        complete: the injected tools-off model seam (:data:`MuseCompleteFn`).
            The ONE thing here that may talk to a network. It owns its own
            endpoint and configuration: this module never infers a model, a
            role or an address (roles resolve by name, never by parsing model
            names).
        controls: the turn budget and caps; :class:`MuseControls` defaults when
            omitted.
        system: OPTIONAL host framing (task t12's identity lane). It is
            **appended** to :data:`MUSE_AUTHORITY`, never substituted for it, so
            no configuration can drop the authority boundary.
        sink: OPTIONAL drain called with each insight as it is produced (task
            t10b hands it a queue). A raising sink is recorded and disabled.
        clock: the ONLY source of a latency measurement. ``None`` — the default
            — leaves every ``latency`` at ``None`` rather than fabricating a
            zero.

    Not thread-safe by itself: run one session at a time per instance. Owning
    the thread, and the lifecycle around it, is task t10b's job.
    """

    def __init__(
        self,
        complete: MuseCompleteFn,
        *,
        controls: Optional[MuseControls] = None,
        system: Optional[str] = None,
        sink: Optional[MuseSink] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self._complete = complete
        self._controls = controls if controls is not None else MuseControls()
        self._system = system
        self._sink = sink
        self._clock = clock
        self._sessions = 0

    @property
    def controls(self) -> MuseControls:
        return self._controls

    @property
    def sessions(self) -> int:
        """How many thinking sessions this loop has started."""
        return self._sessions

    def think(self, boundary: Optional[BoundaryContext]) -> MuseOutcome:
        """Think about *boundary* for at most ``max_turns`` turns. Never raises.

        Every ``Exception`` — from the seam, from the response, from the clock,
        from the drain, from rendering the boundary — becomes a recorded
        :class:`MuseDegradation` on the returned outcome. Only ``BaseException``
        (a Ctrl-C) passes through, because interrupting a host is not a
        degradation.
        """
        self._sessions += 1
        origin = MuseOrigin.of(boundary, session=self._sessions)
        unreadable: list[str] = []
        ctx = _Session(
            complete=self._complete,
            controls=self._controls,
            origin=origin,
            messages=_build_messages(boundary, self._system, self._controls, unreadable),
            sink=self._sink,
            clock=self._clock,
        )
        if unreadable:
            _degrade(
                ctx,
                DEGRADED_UNREADABLE,
                "boundary fields could not be rendered: " + ", ".join(unreadable),
            )
        started = _now(self._clock)
        exit_reason = _think_loop(ctx, self._controls.max_turns)
        return MuseOutcome(
            origin=origin,
            exit_reason=exit_reason,
            insights=list(ctx.insights),
            turns=ctx.turns,
            tokens=ctx.tokens,
            latency=_since(self._clock, started),
            degradations=list(ctx.degradations),
        )

    def __call__(self, boundary: Optional[BoundaryContext]) -> Optional[MuseComment]:
        """Run one session and fold it into a single comment.

        This is the compatibility shim that lets ``MuseLoop`` satisfy the pump's
        existing ``MuseSeam`` unchanged, so t10b can move the pump to the
        drain-shaped seam without a flag day. A degraded or silent session reads
        as ``None`` — the muse simply had nothing to say.
        """
        return self.think(boundary).comment()


# ── prompt building ───────────────────────────────────────────────────────────


def _build_messages(
    boundary: Optional[BoundaryContext],
    system: Optional[str],
    controls: MuseControls,
    unreadable: list[str],
) -> list[dict[str, Any]]:
    """The opening two messages: the authority framing, then the boundary."""
    return [
        {"role": "system", "content": _system_message(system)},
        {"role": "user", "content": _render_boundary(boundary, controls, unreadable)},
    ]


def _system_message(extra: Optional[str]) -> str:
    """:data:`MUSE_AUTHORITY` always first; host framing only ever appended."""
    if extra is None:
        return MUSE_AUTHORITY
    text = _plain(extra).strip()
    return f"{MUSE_AUTHORITY}\n\n{text}" if text else MUSE_AUTHORITY


def _render_boundary(
    boundary: Optional[BoundaryContext],
    controls: MuseControls,
    unreadable: list[str],
) -> str:
    """Render the boundary into prose. Never raises; unreadable fields are NAMED.

    The operator's verbatim request is rendered verbatim — no strip, no
    normalization — because that is the one string the whole perception arc
    exists to preserve.
    """
    cap = controls.max_context_chars
    lines = [_OPENING]

    packet = _attr(boundary, "packet")
    if packet is not None:
        original = _safe_text(packet, "original", unreadable)
        if original:
            lines.append(f"request (verbatim): {_clip(original, cap)}")
        interpretation = _safe_text(packet, "interpretation", unreadable).strip()
        if interpretation:
            lines.append(f"interpretation: {_clip(interpretation, cap)}")

    for label, attr in _CONTEXT_FIELDS:
        value = _safe_text(boundary, attr, unreadable).strip()
        if value:
            lines.append(f"{label}: {_clip(value, cap)}")

    history = _attr(boundary, "history")
    entries = _render_history(history, cap, unreadable)
    if entries:
        lines.append("recent exchange:")
        lines.extend(entries)
    return "\n".join(lines)


def _render_history(history: Any, cap: int, unreadable: list[str]) -> list[str]:
    """Render the trailing history entries; anything unrenderable is skipped."""
    if not isinstance(history, (list, tuple)):
        return []
    lines: list[str] = []
    for entry in list(history)[-_MAX_HISTORY_ENTRIES:]:
        if isinstance(entry, dict):
            role = _clip(_safe_text(entry, "role", unreadable, mapping=True), _MAX_ROLE_LEN)
            content = _clip(_safe_text(entry, "content", unreadable, mapping=True), cap)
        else:
            role, content = "", _clip(_plain(entry, unreadable, "history"), cap)
        content = content.strip()
        if content:
            lines.append(f"  {role.strip()}: {content}" if role.strip() else f"  {content}")
    return lines


# ── content parsing ───────────────────────────────────────────────────────────


def _split_content(content: str, cap: int) -> tuple[str, str]:
    """Split one thinking turn into ``(narration, guidance)``.

    Guidance lines are the ONLY channel into the acting loop, and they arrive as
    plain advisory text — there is no other kind of line this could produce.
    Both halves are capped so a runaway turn cannot flood anything.
    """
    narration: list[str] = []
    guidance: list[str] = []
    for raw in content.splitlines():
        line = _DONE_RE.sub("", raw).strip()
        if not line:
            continue
        match = _GUIDANCE_RE.match(line)
        if match is not None:
            body = line[match.end() :].strip()
            if body:
                guidance.append(body)
            continue
        narration.append(line)
    return _clip("\n".join(narration), cap), _clip("\n".join(guidance), cap)


def _content(response: Any) -> str:
    """The raw completion text off *response*.

    Accepts a :class:`~embodiment.contract.ModelResponse`-shaped object (reads
    ``.content``) or a plain ``str``. Anything else yields ``""`` — a quiet
    turn, not a fault. Called inside :func:`_complete_turn`'s ``try``, so a
    hostile ``content`` property degrades like any other seam failure.
    """
    value = getattr(response, "content", None)
    if value is None and isinstance(response, str):
        value = response
    return value if isinstance(value, str) else ""


def _token_total(response: Any) -> Optional[int]:
    """The turn's reported token count, or ``None`` when nothing was reported.

    ``0`` prompt AND ``0`` completion tokens means *unreported*, not zero: a
    completion always has a prompt, so a zero pair can only be a seam that does
    not report usage. Returning ``None`` there is the whole no-fabricated-zero
    rule (C3) in one line.
    """
    prompt = getattr(response, "prompt_tokens", None)
    completion = getattr(response, "completion_tokens", None)
    if prompt is None and completion is None:
        return None
    return (int(prompt or 0) + int(completion or 0)) or None


def _add_tokens(running: Optional[int], turn: Optional[int]) -> Optional[int]:
    """Sum reported counts; ``None`` + ``None`` stays ``None``, never ``0``."""
    if running is None and turn is None:
        return None
    return (running or 0) + (turn or 0)


# ── small, never-raising helpers ──────────────────────────────────────────────


def _attr(obj: Any, name: str) -> Any:
    """``getattr`` that cannot raise — a hostile property reads as absent."""
    try:
        return getattr(obj, name, None)
    except Exception:  # noqa: BLE001 - an unreadable attribute is simply absent
        return None


def _plain(value: Any, unreadable: Optional[list[str]] = None, label: str = "") -> str:
    """Coerce *value* to text. A value whose ``str()`` raises is NAMED, not hidden."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return str(value)
    except Exception:  # noqa: BLE001 - recorded by the caller as DEGRADED_UNREADABLE
        if unreadable is not None:
            unreadable.append(label or "value")
        return ""


def _safe_text(obj: Any, name: str, unreadable: list[str], *, mapping: bool = False) -> str:
    """Read one context value as text, naming it if it cannot be rendered."""
    try:
        raw = obj.get(name) if mapping else getattr(obj, name, None)
    except Exception:  # noqa: BLE001 - recorded by the caller as DEGRADED_UNREADABLE
        unreadable.append(name)
        return ""
    return _plain(raw, unreadable, name)


def _clip(text: str, cap: Any) -> str:
    """Truncate *text* to *cap* characters; a cap of ``0`` or less disables it."""
    limit = _coerce_int(cap)
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit]


def _coerce_int(value: Any, default: int = 0) -> int:
    """Best-effort ``int``; anything uncoercible falls back to *default*."""
    try:
        return int(value)
    except Exception:  # a junk count is a default, never a crash
        return default


def _now(clock: Optional[Callable[[], float]]) -> Optional[float]:
    """Read *clock*, or ``None`` when absent or itself failing.

    A broken clock degrades the MEASUREMENT only — never the thinking. Mirrors
    :func:`embodiment.perception.perceive`'s treatment exactly.
    """
    if clock is None:
        return None
    try:
        return float(clock())
    except Exception:  # noqa: BLE001 - a clock failure is not a thinking failure
        return None


def _since(clock: Optional[Callable[[], float]], start: Optional[float]) -> Optional[float]:
    """Elapsed time since *start*, or ``None`` when unmeasurable (never ``0.0``)."""
    if start is None:
        return None
    now = _now(clock)
    if now is None:
        return None
    return now - start


def _insight_step(insight: Any) -> int:
    """The acting-loop step an insight was reasoning about. Never raises."""
    origin = _attr(insight, "origin")
    return _coerce_int(_attr(origin, "step_count"))


# ── staleness — the consumer's relevance judgement ────────────────────────────

#: A DEFAULT, not a rule: how many acting-loop steps an insight may fall behind
#: before :func:`is_stale` calls it stale. The consumer's own policy wins — the
#: number that matters is per-host, and this module has no way to know it.
DEFAULT_STALE_LAG = 5


def insight_lag(insight: MuseInsight, *, step_count: Any) -> int:
    """How many acting-loop steps have passed since *insight*'s boundary.

    ``0`` when the insight is current — or when it somehow stamps a step ahead
    of *step_count*, because an insight cannot be stale from behind.
    """
    return max(0, _coerce_int(step_count) - _insight_step(insight))


def is_stale(insight: MuseInsight, *, step_count: Any, max_lag: Any = DEFAULT_STALE_LAG) -> bool:
    """Whether *insight* has fallen more than *max_lag* steps behind. Never raises.

    Deviation d1 is why this exists: the muse thinks in parallel, so an insight
    computed against step 3 can be read at step 40. Judge before you act on one.
    """
    return insight_lag(insight, step_count=step_count) > max(0, _coerce_int(max_lag))
