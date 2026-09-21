"""One spoken turn — the user's transcribed words in, the words Gwen says out.

This is the turn engine of the background voice daemon: given the text a
listener transcribed, produce the text a voice will speak. It owns no audio, no
socket, no clock and no thread. The model seam arrives *injected* as a
``complete`` callable of exactly the shape :func:`embodiment.loop.run` already
expects, so nothing here dials a gateway and the tests drive it with plain
functions.

Built on the loop from day one, on purpose
-------------------------------------------
A spoken reply could be one completion and a ``return``. It is not. The turn is
driven through :func:`embodiment.loop.run` with a
:class:`~embodiment.tools.ToolRegistry` that happens to be **empty**, because
tool use and agent triggering must later arrive as *additions* rather than a
rewrite. Registering a tool changes what the registry holds; it does not change
one line of this module, and ``tests/test_turn.py`` proves that by registering
a fake tool and watching it execute through this same code path.

With nothing registered the arithmetic is the cheapest available: the model
answers in prose, the loop sees a turn that asked for no tool, and — with
:attr:`~embodiment.loop.LoopControls.max_continue_nudges` at zero and the forced
synthesis turn disarmed — exits at :data:`~embodiment.loop.EXIT_STOPPED` after
exactly **one** call to ``complete``. Both controls are set here rather than
left at their defaults precisely because those defaults are tuned for a coding
drive that is *supposed* to finish through a tool, and a conversation is not.

The verbatim invariant
----------------------
The text the model sees as the user turn is **exactly** the caller's input. It
is carried two ways, both structural: it becomes
:attr:`~embodiment.contract.Task.instruction` with every other prompt-shaping
field on the task left empty (so :func:`embodiment.loop.run`'s own
``_build_user_message`` returns it unchanged), and it goes through
:func:`embodiment.perception.perceive` into a
:class:`~embodiment.contract.ContextPacket` whose ``original`` field is
sacrosanct. No perception seam is dialled here: intake with no ``interpret``
callable is a clean, non-degraded packet and costs zero model calls.

Identity
--------
The system prompt is composed through :func:`embodiment.framing.frame_cortex`,
the **acting loop's** framer. With no identity configured that function returns
the base prompt *by name* — the same object, not a copy — so an unconfigured
daemon's prompt is byte-identical to :data:`SYSTEM_PROMPT`. That is the repo's
standing acceptance criterion and it is tested here as well as in
``tests/test_framing.py``.

What can and cannot be detected about truncation
--------------------------------------------------
:class:`~embodiment.contract.ModelResponse` carries no ``finish_reason``
(embodiment#37): a completion the server cut at the token ceiling and one the
model chose to end arrive at the loop as the same object. There is therefore no
exact test available, only a **proxy**, and it is named as one:

*Detected* — a completion whose reported ``completion_tokens`` reaches or
exceeds :attr:`TurnConfig.max_tokens`, i.e. a turn that ran into the ceiling
this config asked for. That is the truncation this daemon is actually able to
cause, which is why it is the one worth proxying.

*Not detected* — truncation by a server-side ceiling lower than
:attr:`TurnConfig.max_tokens` (the config is advisory: this module never puts
``max_tokens`` on a wire, the host's seam does); a cut by a stop sequence, a
content filter or a dropped connection that still returned prose. And the proxy
over-reports: a completion that *deliberately* ends on the exact token it was
capped at is indistinguishable from one that was cut, and is reported as
suspected truncation. Hence the degradation code
:data:`DEGRADED_TRUNCATION_SUSPECTED` — suspected, not measured.

*Not detectable, and SAID SO* — a seam that reports ``completion_tokens`` as
``0`` (or not at all) makes the proxy inert. A docstring saying "not detected"
is not a record: a host reading a clean :class:`TurnResult` would conclude the
words were complete, and if the rig's seam never reports usage the detector
would be dead with nobody the wiser. So a turn that generated something and
reported no usage records :data:`DEGRADED_TRUNCATION_UNDETECTABLE` — **once**,
whatever the turn's completion count, because the fact reported is a property of
the turn. It changes nothing about what is spoken.

The default :attr:`TurnConfig.max_tokens` is deliberately generous (16000) for
the reason ``0.13.0`` measured: a truncated turn is silence, and silence is the
worst failure mode a presence has. Setting it to ``0`` or less is an explicit
host opt-out that disables the ceiling check *and* its uncheckable record —
there is nothing to be uncheckable against.

Tools the model was never shown
--------------------------------
``complete`` is the one-argument seam the loop expects, so tool schemas can only
reach the wire if the host wrapped its seam with
:func:`embodiment.tools.bind_tools`. Forgetting that is the first mistake a tool
author makes and it produces a presence that *has* tools and silently never uses
them, so a non-empty registry whose seam is not bound to it (or is bound to a
different one) records :data:`DEGRADED_TOOLS_UNBOUND`. It is a record, not a
refusal: the turn still runs, because not speaking is worse than speaking
without tools. An empty registry records nothing either way.

Never silence, never a raise
-----------------------------
:func:`turn` does not raise. A seam that dies, a tool that explodes, an empty
completion and a suspected truncation all resolve the same way — a recorded
:class:`TurnDegradation` on the returned result (constraint C3: nothing degrades
silently) and a non-empty string to speak. :attr:`TurnResult.spoken` is never
empty, which is the one promise a voice presence cannot afford to break — and
it is enforced at ONE final point, :func:`_ensure_spoken`, rather than at each
rung. Anything that resolves blank after ``.strip()`` — a whitespace-only
completion, a truncated turn whose partial prose was whitespace, or a
:attr:`TurnConfig.fallback_text` a host configured as ``"   "`` (truthy in
Python, which is how this got in) — speaks :data:`FALLBACK_TEXT` and records
:data:`DEGRADED_FALLBACK_BLANK`.

Having nothing to say has two distinct causes and they get two distinct codes,
because a host debugging them looks in different places:
:data:`DEGRADED_BUDGET_EXHAUSTED` when the model kept calling tools until
``max_steps`` ran out (a tool-surface and budget question) and
:data:`DEGRADED_EMPTY_COMPLETION` when a completion really was empty (a model or
prompt question). Both speak the fallback.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from embodiment.contract import NO_RESULT_PRODUCED, ContextPacket, ModelResponse, Task
from embodiment.framing import frame_cortex
from embodiment.loop import EXIT_BUDGET, LoopAborted, LoopControls, LoopOutcome
from embodiment.loop import run as loop_run
from embodiment.perception import perceive
from embodiment.tools import BOUND_REGISTRY_ATTR, ToolRegistry

__all__ = [
    "SYSTEM_PROMPT",
    "FALLBACK_TEXT",
    "TRUNCATION_SUFFIX",
    "ROLE_SENSES",
    "DEGRADED_BUDGET_EXHAUSTED",
    "DEGRADED_EMPTY_COMPLETION",
    "DEGRADED_FALLBACK_BLANK",
    "DEGRADED_TOOLS_UNBOUND",
    "DEGRADED_TRUNCATION_UNDETECTABLE",
    "DEGRADED_TRUNCATION_SUSPECTED",
    "DEGRADED_SEAM_ABORTED",
    "DEGRADED_PERCEPTION",
    "TurnConfig",
    "TurnDegradation",
    "TurnResult",
    "turn",
]


# ── configuration defaults ────────────────────────────────────────────────────

#: The lobes role name whose model answers a spoken turn. Configuration only:
#: this module resolves nothing and dials nothing — the host builds the seam.
ROLE_SENSES = "senses"

#: The base system prompt for a spoken turn, in Hebrew, because the reference
#: rig converses in Hebrew. It is a plain module constant so a host can swap it
#: on :class:`TurnConfig` without this module growing a branch.
SYSTEM_PROMPT = (
    "את גוון, בת שיחה קולית. את מקשיבה למה שנאמר ועונה בקול.\n"
    "עני בעברית, במשפטים קצרים וטבעיים לדיבור.\n"
    "כל מה שתכתבי ייאמר בקול רם: בלי סימוני עיצוב, בלי רשימות ובלי קוד.\n"
    "אם משהו לא ברור, בקשי הבהרה קצרה במקום לנחש."
)

#: Spoken when the turn produced nothing at all. Never an empty string.
FALLBACK_TEXT = "סליחה, לא הצלחתי לנסח תשובה כרגע."

#: Appended to partial prose when truncation is suspected.
TRUNCATION_SUFFIX = "— נקטעתי באמצע."


# ── the degradation vocabulary (C3) ───────────────────────────────────────────

#: The model returned a turn with no prose and no tool call.
DEGRADED_EMPTY_COMPLETION = "turn-empty-completion"
#: A completion reached the configured token ceiling — see the module docstring
#: on exactly what this proxy can and cannot tell.
DEGRADED_TRUNCATION_SUSPECTED = "turn-truncation-suspected"
#: The configured fallback was blank, so the built-in one was spoken.
DEGRADED_FALLBACK_BLANK = "turn-fallback-blank"
#: The step budget ran out with no prose to speak.
DEGRADED_BUDGET_EXHAUSTED = "turn-budget-exhausted"
#: The seam reported no token usage, so the ceiling proxy could not run.
DEGRADED_TRUNCATION_UNDETECTABLE = "turn-truncation-undetectable"
#: Tools were registered but the seam was never bound to them.
DEGRADED_TOOLS_UNBOUND = "turn-tools-unbound"
#: The injected seam (or the loop driving it) failed mid-turn.
DEGRADED_SEAM_ABORTED = "turn-seam-aborted"
#: Intake degraded; the operator's verbatim words still survived.
DEGRADED_PERCEPTION = "turn-perception-degraded"

#: Cap on one degradation's reason text, as every sibling lane caps its own.
_MAX_REASON_LEN = 500


@dataclass(frozen=True)
class TurnDegradation:
    """One recorded, host-visible turn degradation.

    Field-for-field a prefix of :class:`embodiment.loop.LoopDegradation`,
    :class:`embodiment.perception.PerceptionDegradation` and
    :class:`embodiment.tools.ToolDegradation`, so a host folds ONE shape.
    """

    code: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "reason": self.reason}


@dataclass(frozen=True)
class TurnConfig:
    """What a spoken turn runs under. Every field is a default, not a discovery.

    Args:
        role: the lobes role name whose model answers. Carried so a host and a
            trace agree on which mind spoke; never parsed, never resolved here.
        model: the model id, recorded onto the result's stats.
        engine: the seam label recorded onto the task and its stats.
        system_prompt: the base system prompt, before identity framing.
        max_tokens: the completion ceiling the host's seam is expected to
            request. This module puts nothing on a wire — it uses the number as
            the truncation proxy described in the module docstring. Generous by
            default: a truncated turn is silence. ``0`` or less is an explicit
            opt-out that disables the proxy and its uncheckable record together.
        max_steps: the loop's model-turn budget. Above one so a registered tool
            has room to run and be spoken about, while an empty registry still
            terminates in exactly one completion.
        fallback_text: spoken when the turn produced nothing. A blank or
            whitespace-only value is a misconfiguration: :data:`FALLBACK_TEXT`
            is spoken instead and :data:`DEGRADED_FALLBACK_BLANK` is recorded.
        truncation_suffix: appended to partial prose on a suspected truncation.
        identity: the resolved identity, or ``None``. ``None`` leaves the system
            prompt byte-identical to *system_prompt*.
        repo_path: the working path stamped onto the task.
    """

    role: str = ROLE_SENSES
    model: str = ""
    engine: str = "lobes"
    system_prompt: str = SYSTEM_PROMPT
    max_tokens: int = 16000
    max_steps: int = 8
    fallback_text: str = FALLBACK_TEXT
    truncation_suffix: str = TRUNCATION_SUFFIX
    identity: Optional[str] = None
    repo_path: str = "."


@dataclass(frozen=True)
class TurnResult:
    """What one spoken turn produced.

    :attr:`spoken` is never empty — that is the point of the whole module.
    """

    spoken: str
    degradations: tuple[TurnDegradation, ...] = ()
    tool_calls: tuple[str, ...] = ()
    steps: int = 0
    exit_reason: str = ""
    packet: Optional[ContextPacket] = None

    @property
    def degraded(self) -> bool:
        """True iff anything about this turn degraded."""
        return bool(self.degradations)

    def to_dict(self) -> dict[str, Any]:
        return {
            "spoken": self.spoken,
            "degradations": [d.to_dict() for d in self.degradations],
            "tool_calls": list(self.tool_calls),
            "steps": self.steps,
            "exit_reason": self.exit_reason,
        }


# ── the model-seam recorder ───────────────────────────────────────────────────


@dataclass
class _Recorder:
    """Wraps the injected ``complete`` to keep every :class:`ModelResponse`.

    The loop hands a response to nobody but itself, and the truncation proxy
    needs ``completion_tokens``. Wrapping the seam is the only way to see them
    without touching ``loop.py`` — which stays byte-identical, and is tested to.

    It is a pass-through in every other respect: it calls, it appends, it
    returns. It catches nothing, so a seam failure still reaches the loop's own
    abort path exactly as an unwrapped seam would.
    """

    complete: Callable[[list[dict[str, Any]]], ModelResponse]
    responses: list[ModelResponse] = field(default_factory=list)

    def __call__(self, messages: list[dict[str, Any]]) -> ModelResponse:
        response = self.complete(messages)
        self.responses.append(response)
        return response

    def hit_ceiling(self, max_tokens: int) -> Optional[ModelResponse]:
        """The first response whose reported spend reached *max_tokens*, if any.

        ``completion_tokens`` of ``0`` means "not reported", never "spent
        nothing measurable" — a seam that reports no usage is unmeasurable here,
        and this returns ``None`` rather than guessing. That unmeasurability is
        itself recorded; see :meth:`usage_unreported`.
        """
        if max_tokens <= 0:
            return None
        for response in self.responses:
            if _spent(response) >= max_tokens:
                return response
        return None

    def usage_unreported(self) -> bool:
        """True iff any response that GENERATED something reported no usage.

        The ceiling proxy is the only truncation signal available, and a seam
        that reports no ``completion_tokens`` makes it inert. An inert detector
        that says nothing is a silent degradation — a host would read a clean
        :class:`TurnResult` and conclude the words were complete — so this is
        what :func:`turn` records instead.

        A completion that generated nothing at all is excluded: there was
        nothing to truncate, and the empty completion is already its own record.
        """
        return any(
            _spent(response) <= 0 and (response.content or response.tool_calls)
            for response in self.responses
        )


# ── the public entry point ────────────────────────────────────────────────────


def turn(
    text: str,
    complete: Callable[[list[dict[str, Any]]], ModelResponse],
    *,
    tools: Optional[ToolRegistry] = None,
    config: Optional[TurnConfig] = None,
) -> TurnResult:
    """Drive ONE spoken turn. Never raises; :attr:`TurnResult.spoken` is never empty.

    Args:
        text: the listener's transcription, used **verbatim**.
        complete: the injected model seam — one call, one model turn — of
            exactly the shape :func:`embodiment.loop.run` expects. Build it from
            a schema-aware seam with :func:`embodiment.tools.bind_tools`.
        tools: the tool surface. ``None`` — the default — is an empty
            :class:`~embodiment.tools.ToolRegistry`: no tool schema reaches the
            wire and the turn ends in one completion. A non-empty registry whose
            tools never reached *complete* is recorded, not refused; see
            :data:`DEGRADED_TOOLS_UNBOUND`.
        config: what the turn runs under; defaults to :class:`TurnConfig`.

    Returns:
        A :class:`TurnResult` carrying the words to speak, every degradation the
        turn recorded, the tools it called and the model turns it spent.
    """
    cfg = config or TurnConfig()
    registry = tools if tools is not None else ToolRegistry()
    degradations: list[TurnDegradation] = []

    _check_binding(complete, registry, degradations)
    packet = _perceive(text, degradations)
    task = Task.new(
        cfg.repo_path,
        text,
        engine=cfg.engine,
        context_packet=packet,
    )
    recorder = _Recorder(complete=complete)

    outcome, aborted = _drive(recorder, task, registry, cfg)
    if aborted is not None:
        degradations.append(_degradation(DEGRADED_SEAM_ABORTED, aborted))

    degradations.extend(
        TurnDegradation(code=entry.code, reason=_short(entry.reason))
        for entry in (outcome.degradations if outcome is not None else [])
    )
    degradations.extend(
        TurnDegradation(code=entry.code, reason=_short(entry.reason))
        for entry in registry.degradations
    )

    spoken = _speak(outcome, recorder, cfg, degradations, aborted=aborted)
    return TurnResult(
        spoken=spoken,
        degradations=tuple(degradations),
        tool_calls=_tool_calls(outcome),
        steps=_model_turns(outcome),
        exit_reason=outcome.exit_reason if outcome is not None else "",
        packet=packet,
    )


# ── internals ─────────────────────────────────────────────────────────────────


def _check_binding(
    complete: Callable[[list[dict[str, Any]]], ModelResponse],
    registry: ToolRegistry,
    degradations: list[TurnDegradation],
) -> None:
    """Record a registry whose tools the model will never be shown.

    :func:`turn` takes the one-argument seam :func:`embodiment.loop.run`
    expects, so the schemas can only reach the wire if the host wrapped its
    seam with :func:`embodiment.tools.bind_tools`. Forgetting that produces a
    presence that *has* tools and never uses them, with nothing anywhere saying
    why — the exact silent degradation C3 forbids. So the wiring is checked,
    recorded, and otherwise left alone: the turn still runs, because refusing to
    speak is a worse failure than speaking without tools.

    An empty registry records nothing, bound or not: there is nothing the model
    was not shown.
    """
    if registry.empty:
        return
    bound = getattr(complete, BOUND_REGISTRY_ATTR, None)
    if bound is registry:
        return
    where = "bound to another registry" if isinstance(bound, ToolRegistry) else "not bound"
    degradations.append(
        TurnDegradation(
            DEGRADED_TOOLS_UNBOUND,
            _short(
                f"the registry holds {len(registry)} tool(s) the model was never "
                f"shown: the seam is {where}. Wrap it with tools.bind_tools("
                "seam, registry)."
            ),
        )
    )


def _perceive(text: str, degradations: list[TurnDegradation]) -> ContextPacket:
    """Intake the caller's words verbatim. :func:`perceive` never raises."""
    packet, record = perceive(text)
    if record.degraded:
        degradations.append(TurnDegradation(DEGRADED_PERCEPTION, "intake degraded"))
    return packet


def _controls() -> LoopControls:
    """The conversation's loop knobs — a single completion when no tool is used.

    ``max_continue_nudges=0`` because a prose turn IS the answer here, not a
    model trailing off mid-task; ``synthesis=False`` because the prose is
    already the summary and a second turn would only restate it;
    ``incompletion``/``write_intent`` off because a conversation changes no file
    and a "did not deliver" flag would be a false reading of a spoken reply.
    """
    return LoopControls(
        max_continue_nudges=0,
        synthesis=False,
        incompletion=False,
        write_intent=False,
    )


def _drive(
    recorder: _Recorder,
    task: Task,
    registry: ToolRegistry,
    cfg: TurnConfig,
) -> tuple[Optional[LoopOutcome], Optional[BaseException]]:
    """Run the loop; return ``(outcome_or_None, failure_or_None)``. Never raises."""
    system_prompt = frame_cortex(cfg.system_prompt, identity=cfg.identity)
    try:
        return (
            loop_run(
                recorder,
                task,
                executor=registry,
                max_steps=max(1, cfg.max_steps),
                system_prompt=system_prompt,
                controls=_controls(),
                model=cfg.model,
            ),
            None,
        )
    except LoopAborted as exc:
        # The loop finalized the partial work before re-raising, so the outcome
        # is usable: whatever the model managed to say is still speakable.
        return exc.outcome, exc
    except Exception as exc:  # noqa: BLE001  # recorded by the caller; a turn never raises
        # Not reachable through any path the loop documents — it contains its
        # own seam failures as LoopAborted. Recorded rather than swallowed
        # (constraint C3) so an unforeseen harness fault is visible to the host
        # instead of arriving as an unexplained fallback line.
        return None, exc


def _speak(
    outcome: Optional[LoopOutcome],
    recorder: _Recorder,
    cfg: TurnConfig,
    degradations: list[TurnDegradation],
    *,
    aborted: Optional[BaseException],
) -> str:
    """Resolve the words to speak, recording why when they are not the model's.

    Three rungs, in order: the model's prose (plus a truncation suffix when the
    ceiling was hit), the prose alone, or the configured fallback. The last two
    rungs each append a degradation, so a spoken fallback is never silent about
    being one.

    The truncation ladder has three states, not two: *suspected* (the ceiling
    was reached), *checked and clear* (usage was reported and stayed under it),
    and *uncheckable* (the seam reported no usage at all). Only the third is new
    to a reader, and it is the one that used to say nothing.

    Whatever the rungs resolve to passes through :func:`_ensure_spoken`, the ONE
    point at which the words are final.
    """
    return _ensure_spoken(
        _resolve(outcome, recorder, cfg, degradations, aborted=aborted), cfg, degradations
    )


def _resolve(
    outcome: Optional[LoopOutcome],
    recorder: _Recorder,
    cfg: TurnConfig,
    degradations: list[TurnDegradation],
    *,
    aborted: Optional[BaseException],
) -> str:
    """The rung ladder. Its answer is a candidate, not the final word."""
    prose = _prose(outcome, recorder, aborted=aborted)
    ceiling = recorder.hit_ceiling(cfg.max_tokens)
    if ceiling is not None:
        degradations.append(
            TurnDegradation(
                DEGRADED_TRUNCATION_SUSPECTED,
                _short(
                    f"completion_tokens={ceiling.completion_tokens} reached "
                    f"max_tokens={cfg.max_tokens}; ModelResponse carries no "
                    "finish_reason (embodiment#37), so this is a proxy"
                ),
            )
        )
    elif cfg.max_tokens > 0 and recorder.usage_unreported():
        # ONE record per turn however many completions it made: the fact being
        # reported is "this turn's truncation could not be checked", which is a
        # property of the turn, not of each call.
        degradations.append(
            TurnDegradation(
                DEGRADED_TRUNCATION_UNDETECTABLE,
                _short(
                    "the seam reported no completion_tokens, so truncation "
                    f"against max_tokens={cfg.max_tokens} could not be checked; "
                    "ModelResponse carries no finish_reason (embodiment#37), so "
                    "there is no other signal available"
                ),
            )
        )
    if ceiling is not None and prose:
        return f"{prose} {cfg.truncation_suffix}".strip()
    if prose:
        return prose
    degradations.append(_no_prose(outcome))
    return cfg.fallback_text


def _ensure_spoken(
    text: str,
    cfg: TurnConfig,
    degradations: list[TurnDegradation],
) -> str:
    """The ONE point where the words are final. Blank in, built-in fallback out.

    Every rung above is a *candidate*. This is the gate, and it is deliberately
    the only one: a check applied at the fallback lookup alone would still let
    silence through from a whitespace-only completion, a truncation whose
    partial prose is whitespace, or a blank
    :attr:`TurnConfig.truncation_suffix`. ``"   "`` is truthy in Python, which
    is precisely how the bug this exists to stop got in.

    A blank result is always a host misconfiguration by the time it reaches
    here, so it is recorded (constraint C3) rather than quietly repaired.
    """
    if text.strip():
        return text
    blank_config = not (cfg.fallback_text or "").strip()
    cause = (
        "the configured fallback_text is blank" if blank_config else "the resolved text was blank"
    )
    degradations.append(
        TurnDegradation(
            DEGRADED_FALLBACK_BLANK,
            _short(f"{cause}, so the built-in fallback was spoken instead of silence"),
        )
    )
    return FALLBACK_TEXT


def _no_prose(outcome: Optional[LoopOutcome]) -> TurnDegradation:
    """Why this turn had nothing to say — the budget, or a genuinely empty turn.

    The two are different faults and a host debugging them looks in different
    places: a budget exit means the model kept calling tools and never answered,
    which is about the tool surface and the step budget, while an empty
    completion is about the model or the prompt. Reporting the first as the
    second sends the reader to the wrong file.
    """
    if outcome is not None and outcome.exit_reason == EXIT_BUDGET:
        return TurnDegradation(
            DEGRADED_BUDGET_EXHAUSTED,
            _short(
                f"the step budget ran out after {outcome.result.stats.model_turns} "
                f"model turn(s) and {len(outcome.result.steps)} tool call(s) with no "
                "prose to speak"
            ),
        )
    return TurnDegradation(DEGRADED_EMPTY_COMPLETION, "the turn produced no prose to speak")


def _prose(
    outcome: Optional[LoopOutcome],
    recorder: _Recorder,
    *,
    aborted: Optional[BaseException],
) -> str:
    """The model's own words, or ``""`` when the drive produced none.

    On an aborted drive the loop stamps a diagnostic summary ("aborted after N
    step(s): …") onto the partial result. That string is for an operator's
    ledger, not for a speaker, so an abort reads the model's own recorded prose
    instead and falls through to the fallback when there is none.
    """
    if aborted is not None:
        spoken = [(r.content or "").strip() for r in recorder.responses]
        return next((text for text in reversed(spoken) if text), "")
    if outcome is None:
        return ""
    summary = (outcome.result.summary or "").strip()
    return "" if summary == NO_RESULT_PRODUCED else summary


def _tool_calls(outcome: Optional[LoopOutcome]) -> tuple[str, ...]:
    """The tool names this turn called, in order."""
    if outcome is None:
        return ()
    return tuple(step.tool for step in outcome.result.steps)


def _model_turns(outcome: Optional[LoopOutcome]) -> int:
    """How many completions the turn spent."""
    return 0 if outcome is None else outcome.result.stats.model_turns


def _degradation(code: str, exc: BaseException) -> TurnDegradation:
    return TurnDegradation(code=code, reason=_short(f"{type(exc).__name__}: {exc}"))


def _spent(response: ModelResponse) -> int:
    """Reported completion tokens; ``0`` means UNREPORTED, never measured-zero."""
    try:
        return int(getattr(response, "completion_tokens", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _short(reason: str) -> str:
    return str(reason)[:_MAX_REASON_LEN]
