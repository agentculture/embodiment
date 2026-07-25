"""embodiment — the loop and presence layer for an app with an AI presence.

**Library first, CLI second.** An app installs this package, supplies a model
seam plus IO callbacks, and gets the perceive → decide → act loop and the
presence pump that keeps the app feeling attended-to between acts::

    from embodiment import run, ToolExecutor, PresenceEngine

    outcome = run(complete, task, executor=my_executor, max_steps=20)
    print(outcome.result.summary, outcome.exit_reason)

This is **software presence, not a robot body** — an app gains a loop and a
presence, not physical embodiment. (In this mesh ``reachy-mini-cli`` owns the
body and ``reachy-lobes`` its local brain.)

Every public name below resolves **lazily** (:pep:`562`), so importing this
package costs nothing but this module: a consumer that only wants
:func:`~embodiment.loop.run` never pays to import the presence engine, and the
``embodiment`` CLI never pays to import the loop. Attribute access imports the
owning submodule once and caches the result in module globals, so every later
lookup is an ordinary attribute hit.

``__version__`` is lazy for the same reason — resolving it pulls
``importlib.metadata`` (and transitively ``email`` and ``inspect``), which is
the single largest import cost in this package and pure waste for a library
consumer that never reads a version string. (The CLI *does* read it, to build
``--version``, so the CLI still pays.)

Anything not re-exported here stays on its module —
``from embodiment import continuity`` then ``continuity.remember(...)``. The
continuity adapter's verbs (``remember`` / ``recall`` / ``assess`` / ``probe``)
are deliberately *not* hoisted: they read far too generically at package level,
and the seam a host actually injects is :data:`ContinuityFn` with the three
:data:`Boundary` points, which are re-exported.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

#: Submodules reachable as ``from embodiment import <name>``.
_SUBMODULES = frozenset(
    {
        "context",
        "continuity",
        "contract",
        "events",
        "framing",
        "identity",
        "loop",
        "media",
        "muse",
        "muse_runner",
        "perception",
        "presence",
        "presence_engine",
    }
)

#: Curated public name → owning submodule. Deliberately a *curation*, not a
#: dump: the ~130 names these modules export between them stay reachable on the
#: modules themselves. ``PresenceSink`` resolves from ``presence_engine``, which
#: owns the presence contract; ``loop`` re-declares a structurally identical
#: protocol so the dependency runs one way (presence consumes the loop, never
#: the reverse), and structural typing makes the two interchangeable.
_LAZY_NAMES = {
    # ── the loop ──────────────────────────────────────────────────────────
    "run": "loop",
    "LoopOutcome": "loop",
    "LoopAborted": "loop",
    "LoopControls": "loop",
    "CompleteFn": "loop",
    "EXIT_FINISHED": "loop",
    "EXIT_STOPPED": "loop",
    "EXIT_BUDGET": "loop",
    # ── the tool seam (a host must supply one) ────────────────────────────
    "ToolExecutor": "loop",
    "ToolOutcome": "loop",
    "ToolError": "loop",
    "UnknownToolError": "loop",
    # ── hooks: only pre_tool is control-bearing ───────────────────────────
    "HookFn": "loop",
    "HookEvent": "loop",
    "HookDecision": "loop",
    "HookFiring": "loop",
    "HOOK_EVENTS": "loop",
    "CONTROL_BEARING_EVENTS": "loop",
    "DECISION_ALLOW": "loop",
    "DECISION_DENY": "loop",
    "DECISION_REWRITE": "loop",
    "DECISION_OBSERVE": "loop",
    # ── observability; every degradation is host-visible (C3) ─────────────
    "LoopEvent": "loop",
    "LoopDegradation": "loop",
    "ObserverFn": "loop",
    "ProgressFn": "loop",
    "OperatorInboxFn": "loop",
    # ── continuity seam (the adapter's own verbs stay on the module) ──────
    "ContinuityFn": "loop",
    "Boundary": "loop",
    "BOUNDARY_ACTION": "loop",
    "BOUNDARY_COMPLETION": "loop",
    "BOUNDARY_MEMORY": "loop",
    # ── presence ──────────────────────────────────────────────────────────
    "PresenceEngine": "presence_engine",
    "PresenceIO": "presence_engine",
    "PresenceSink": "presence_engine",
    "PresenceExecutor": "presence_engine",
    "PresenceTurn": "presence_engine",
    "build_presence_executor": "presence_engine",
    "MuseSeam": "presence_engine",
    # t7's original synchronous callable. Still accepted and adapted onto the
    # drain shape internally, so a host written against it keeps working.
    "MusePullSeam": "presence_engine",
    "MuseComment": "presence_engine",
    "BoundaryContext": "presence_engine",
    "DEFAULT_SPEAKER": "presence_engine",
    # ── the muse: a bounded, tools-off thinking loop (deviation d1) ───────
    # Advisory only. `MUSE_AUTHORITY` resolves from `muse`, which owns it;
    # `framing` re-exports the same object so a host composes against one copy.
    "MuseLoop": "muse",
    "MuseControls": "muse",
    "MuseInsight": "muse",
    "MuseOutcome": "muse",
    "MuseOrigin": "muse",
    "MuseDegradation": "muse",
    "MuseCompleteFn": "muse",
    "MuseSink": "muse",
    "MUSE_AUTHORITY": "muse",
    # Staleness: a parallel loop's insight can arrive long after the step it
    # reasoned about, so relevance is the consumer's judgement to make.
    "insight_lag": "muse",
    "is_stale": "muse",
    # ── the muse runner: the one place embodiment owns a thread ───────────
    "ThreadedMuseRunner": "muse_runner",
    "ThreadFactory": "muse_runner",
    # ── event emission (embodiment#4) — optional, absent by default ───────
    # embodiment produces; `events-cli` owns the envelope contract (c33).
    "EventEmitter": "events",
    "EventDegradation": "events",
    # ── Gwen framing: pure composition, absent identity ⇒ identical prompts ─
    "Framing": "framing",
    "frame_cortex": "framing",
    "frame_subagent": "framing",
    "frame_muse": "framing",
    "muse_system_message": "framing",
    "speaker_label": "framing",
    "unframe": "framing",
    "is_configured": "framing",
    "ROLE_CORTEX": "framing",
    "ROLE_SUBAGENT": "framing",
    "ROLE_MUSE": "framing",
    # ── presence policy (pure; no IO, no clock) ───────────────────────────
    "UpdateCadence": "presence",
    "ClarifyPolicy": "presence",
    "should_update": "presence",
    "should_clarify": "presence",
    "cadence_from_env": "presence",
    "clarify_from_env": "presence",
    "is_go_word": "presence",
    # ── perception (the verbatim invariant) ───────────────────────────────
    "perceive": "perception",
    # ── the shapes every seam exchanges ───────────────────────────────────
    "Task": "contract",
    "TaskResult": "contract",
    "Step": "contract",
    "ContextPacket": "contract",
    "ModelResponse": "contract",
    "ToolCall": "contract",
    "WorkAborted": "contract",
    "OK": "contract",
    "ERROR": "contract",
    "INCOMPLETE": "contract",
    # ── identity (explicit configuration only, never inferred) ────────────
    "resolve_identity": "identity",
}

__all__ = ["__version__", *sorted(_SUBMODULES), *sorted(_LAZY_NAMES)]


def _resolve_version() -> str:
    """Read the installed distribution version, or ``0.0.0`` when unpackaged."""
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as pkg_version

    try:
        return pkg_version("embodiment")
    except PackageNotFoundError:  # pragma: no cover - editable install, no metadata
        return "0.0.0"


def __getattr__(name: str) -> Any:
    """Resolve a public name on first access (:pep:`562`), then cache it.

    Caching into ``globals()`` means only the *first* lookup pays the import;
    every later one is an ordinary module-attribute hit that never reaches here.
    """
    if name == "__version__":
        value: Any = _resolve_version()
    elif name in _SUBMODULES:
        value = importlib.import_module(f"{__name__}.{name}")
    else:
        module = _LAZY_NAMES.get(name)
        if module is None:
            raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
        value = getattr(importlib.import_module(f"{__name__}.{module}"), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Expose the curated surface to ``dir()`` and tab-completion."""
    return sorted(__all__)


if TYPE_CHECKING:  # pragma: no cover - type-checker visibility for the lazy names
    from embodiment import (  # noqa: F401
        context,
        continuity,
        contract,
        events,
        framing,
        identity,
        loop,
        media,
        muse,
        muse_runner,
        perception,
        presence,
        presence_engine,
    )
    from embodiment.contract import (  # noqa: F401
        ERROR,
        INCOMPLETE,
        OK,
        ContextPacket,
        ModelResponse,
        Step,
        Task,
        TaskResult,
        ToolCall,
        WorkAborted,
    )
    from embodiment.events import EventDegradation, EventEmitter  # noqa: F401
    from embodiment.framing import (  # noqa: F401
        ROLE_CORTEX,
        ROLE_MUSE,
        ROLE_SUBAGENT,
        Framing,
        frame_cortex,
        frame_muse,
        frame_subagent,
        is_configured,
        muse_system_message,
        speaker_label,
        unframe,
    )
    from embodiment.identity import resolve_identity  # noqa: F401
    from embodiment.loop import (  # noqa: F401
        BOUNDARY_ACTION,
        BOUNDARY_COMPLETION,
        BOUNDARY_MEMORY,
        CONTROL_BEARING_EVENTS,
        DECISION_ALLOW,
        DECISION_DENY,
        DECISION_OBSERVE,
        DECISION_REWRITE,
        EXIT_BUDGET,
        EXIT_FINISHED,
        EXIT_STOPPED,
        HOOK_EVENTS,
        Boundary,
        CompleteFn,
        ContinuityFn,
        HookDecision,
        HookEvent,
        HookFiring,
        HookFn,
        LoopAborted,
        LoopControls,
        LoopDegradation,
        LoopEvent,
        LoopOutcome,
        ObserverFn,
        OperatorInboxFn,
        ProgressFn,
        ToolError,
        ToolExecutor,
        ToolOutcome,
        UnknownToolError,
        run,
    )
    from embodiment.muse import (  # noqa: F401
        MUSE_AUTHORITY,
        MuseCompleteFn,
        MuseControls,
        MuseDegradation,
        MuseInsight,
        MuseLoop,
        MuseOrigin,
        MuseOutcome,
        MuseSink,
        insight_lag,
        is_stale,
    )
    from embodiment.muse_runner import ThreadedMuseRunner, ThreadFactory  # noqa: F401
    from embodiment.perception import perceive  # noqa: F401
    from embodiment.presence import (  # noqa: F401
        ClarifyPolicy,
        UpdateCadence,
        cadence_from_env,
        clarify_from_env,
        is_go_word,
        should_clarify,
        should_update,
    )
    from embodiment.presence_engine import (  # noqa: F401
        DEFAULT_SPEAKER,
        BoundaryContext,
        MuseComment,
        MusePullSeam,
        MuseSeam,
        PresenceEngine,
        PresenceExecutor,
        PresenceIO,
        PresenceSink,
        PresenceTurn,
        build_presence_executor,
    )
