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
``max_tokens`` on a wire, the host's seam does); truncation when the seam
reports ``completion_tokens`` as ``0`` or does not report usage at all; a cut by
a stop sequence, a content filter or a dropped connection that still returned
prose. And the proxy over-reports: a completion that *deliberately* ends on the
exact token it was capped at is indistinguishable from one that was cut, and is
reported as suspected truncation. Hence the degradation code
:data:`DEGRADED_TRUNCATION_SUSPECTED` — suspected, not measured.

The default :attr:`TurnConfig.max_tokens` is deliberately generous (16000) for
the reason ``0.13.0`` measured: a truncated turn is silence, and silence is the
worst failure mode a presence has.

Never silence, never a raise
-----------------------------
:func:`turn` does not raise. A seam that dies, a tool that explodes, an empty
completion and a suspected truncation all resolve the same way — a recorded
:class:`TurnDegradation` on the returned result (constraint C3: nothing degrades
silently) and a non-empty string to speak. :attr:`TurnResult.spoken` is never
empty, which is the one promise a voice presence cannot afford to break.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from embodiment.contract import NO_RESULT_PRODUCED, ContextPacket, ModelResponse, Task
from embodiment.framing import frame_cortex
from embodiment.loop import LoopAborted, LoopControls, LoopOutcome
from embodiment.loop import run as loop_run
from embodiment.perception import perceive
from embodiment.tools import ToolRegistry

__all__ = [
    "SYSTEM_PROMPT",
    "FALLBACK_TEXT",
    "TRUNCATION_SUFFIX",
    "ROLE_SENSES",
    "DEGRADED_EMPTY_COMPLETION",
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
            default: a truncated turn is silence.
        max_steps: the loop's model-turn budget. Above one so a registered tool
            has room to run and be spoken about, while an empty registry still
            terminates in exactly one completion.
        fallback_text: spoken when the turn produced nothing.
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
        and this returns ``None`` rather than guessing.
        """
        if max_tokens <= 0:
            return None
        for response in self.responses:
            spent = getattr(response, "completion_tokens", 0) or 0
            if spent >= max_tokens:
                return response
        return None


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
            wire and the turn ends in one completion.
        config: what the turn runs under; defaults to :class:`TurnConfig`.

    Returns:
        A :class:`TurnResult` carrying the words to speak, every degradation the
        turn recorded, the tools it called and the model turns it spent.
    """
    cfg = config or TurnConfig()
    registry = tools if tools is not None else ToolRegistry()
    degradations: list[TurnDegradation] = []

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
    """
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
        if prose:
            return f"{prose} {cfg.truncation_suffix}".strip()
    if prose:
        return prose
    degradations.append(
        TurnDegradation(DEGRADED_EMPTY_COMPLETION, "the turn produced no prose to speak")
    )
    return cfg.fallback_text or FALLBACK_TEXT


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


def _short(reason: str) -> str:
    return str(reason)[:_MAX_REASON_LEN]
