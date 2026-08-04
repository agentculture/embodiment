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
        "capability",
        "config_change",
        "config_lifecycle",
        "context",
        "continuity",
        "contract",
        "drone",
        "events",
        "framing",
        "identity",
        "ledger",
        "lifecycle",
        "loop",
        "media",
        "muse_pad",
        "perception",
        "presence",
        "presence_engine",
        "recall_bundle",
        "scope",
        "scoped_run",
        "scratchpad",
        "senses_text",
        "strategist_runner",
        "subagent",
        "workspace",
    }
)

#: Modules that have LEFT the shipped reference architecture but stay readable.
#:
#: The muse was archived on 2026-08-03 (embodiment#53, deviations ``d2``/``d3``,
#: superseding confirmed claims ``c12`` and ``c32``), following ``d15``'s
#: muse-off reference rig. "Archived" here means *off the curated surface*, not
#: deleted and not deprecated: :mod:`embodiment.strategist_runner` was copied out
#: of ``muse_runner.py`` verbatim under the cite-don't-import policy, so the
#: source has to stay openable for the citation to mean anything, and a host
#: that wants counsel can still wire one.
#:
#: The practical difference is one of advertisement. These names resolve through
#: :func:`__getattr__` exactly as a curated submodule does, so
#: ``from embodiment import muse`` still works — but they are absent from
#: :data:`__all__`, absent from :func:`dir`, and none of the eighteen ``Muse*``
#: names they own is hoisted any more, so ``from embodiment import
#: ThreadedMuseRunner`` does not resolve. Reaching the archived lane means
#: naming it. ``tests/test_muse_archival.py`` holds the whole disposition.
ARCHIVED_SUBMODULES: tuple[str, ...] = ("muse", "muse_runner")

#: Every submodule :func:`__getattr__` will import — curated plus archived.
_RESOLVABLE = _SUBMODULES | frozenset(ARCHIVED_SUBMODULES)

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
    # The pump's advisory SEAM, which survives the muse's archival: it is a
    # structural protocol owned by `presence_engine`, satisfied by anything
    # with the drain shape, and it never imported `muse` in the first place.
    # The name is historical; the archived lane is `embodiment.muse`.
    "MuseSeam": "presence_engine",
    # t7's original synchronous callable. Still accepted and adapted onto the
    # drain shape internally, so a host written against it keeps working.
    "MusePullSeam": "presence_engine",
    "MuseComment": "presence_engine",
    "BoundaryContext": "presence_engine",
    "DEFAULT_SPEAKER": "presence_engine",
    # ── strategic scope: the strategist tier's protocol shapes ────────────
    # What the strategist and the worker exchange. Read the directive's field
    # list as an exclusion as much as an inclusion: no `tool`, no `arguments`,
    # no `command`, no `approve` — a directive can only ever carry scope, and
    # `FORBIDDEN_DIRECTIVE_KEYS` is the ban made checkable. The `DEGRADED_*` /
    # `DROPPED_*` codes stay on their modules, as every other lane's do: they
    # collide across lanes by design and `ledger.known_codes()` is the surface
    # for reading them.
    "ScopeDirective": "scope",
    "ScopeResponsibility": "scope",
    "ScopeSnapshot": "scope",
    "ScopeReport": "scope",
    "ScopeControls": "scope",
    "ScopeDegradation": "scope",
    "ScopeRejection": "scope",
    "ScopeOutcome": "scope",
    "ScopeCompleteFn": "scope",
    "ScopeRegister": "scope",
    "ScopeLoop": "scope",
    "directive_from_payload": "scope",
    "SCOPE_AUTHORITY": "scope",
    "FORBIDDEN_DIRECTIVE_KEYS": "scope",
    "MARKER_DIRECTIVE": "scope",
    "MARKER_HOLD": "scope",
    "REFUSAL_CODES": "scope",
    "LANE_DURABLE": "scope",
    "LANE_SESSION": "scope",
    "SCOPE_LANES": "scope",
    # The strategist's own THINKING tools, on the `MuseToolBench` precedent:
    # absent by default, wired explicitly by a host, never a default flip.
    "ScopeToolBench": "scope",
    "ScopeToolCompleteFn": "scope",
    "ScopeToolExecuteFn": "scope",
    # ── the governor: scope applied to one acting drive ───────────────────
    # `ScopeGovernor(strategist=None)` is byte-identical to `run()` — an
    # unarmed governor is the same composition an actor-only host gets by
    # never touching this lane at all.
    "ScopeGovernor": "scoped_run",
    "ScopedOutcome": "scoped_run",
    "ScopedControls": "scoped_run",
    "ScopeContext": "scoped_run",
    "ScopeTransition": "scoped_run",
    "ScopeProjectorFn": "scoped_run",
    "ScopePersistence": "scoped_run",
    "ScopeSession": "scoped_run",
    "run_scoped": "scoped_run",
    "render_directive": "scoped_run",
    # ── the strategist's thread: the second place embodiment owns one ─────
    # Cited from the archived `muse_runner`, then owned outright (`d3`/`d4`).
    "StrategistRunner": "strategist_runner",
    "StrategistLimits": "strategist_runner",
    "STRATEGIST_ROLE": "strategist_runner",
    # ── the pad: a thinking lane's write-only working memory ──────────────
    # Built for the muse's bench (task t12) and still named for it, but the pad
    # itself is not archived: it reuses scratchpad's KINDS and schemas
    # unchanged, `finish` is the one declared omission, and it is a bench any
    # thinking lane can be handed. Not a memory: no recall surface reaches it
    # (claim c10). `MusePadCounts` carries the protocol-adherence counters the
    # pad validation reads. The BENCH TYPE it satisfies lives on the archived
    # `embodiment.muse`, which is why this module still imports it.
    "MusePad": "muse_pad",
    "MusePadCounts": "muse_pad",
    "MUSE_PAD_TOOLS": "muse_pad",
    "MUSE_PAD_PROTOCOL": "muse_pad",
    # ── the muse's workspace: one command, in a container that reaches nothing ─
    # The second thing on that bench (task t13), and the first module here that
    # imports a sibling CLI's library surface at module scope (`headspace.api`,
    # the only supported one). No repo, no store, no network, and NO secrets
    # parameter at all — the leak path cannot be opened by configuration (c34).
    "MuseWorkspace": "workspace",
    "WorkspaceCounts": "workspace",
    "WorkspaceDegradation": "workspace",
    "WORKSPACE_TOOLS": "workspace",
    "WORKSPACE_PROTOCOL": "workspace",
    # NOTE — the muse lane used to be hoisted here (`ThreadedMuseRunner`,
    # `ThreadFactory`, `MuseDelivery` from `muse_runner`, and fifteen names
    # from `muse`). All eighteen were retired on 2026-08-03 with the archival:
    # see `ARCHIVED_SUBMODULES` above. They stay importable from
    # `embodiment.muse` / `embodiment.muse_runner` by name.
    # ── drones: authored once, then run on code plus tens of tokens ───────
    # The verbs (`create` / `evoke` / `catalog`) stay on the module — they read
    # far too generically at package level, the same reason continuity's
    # remember/recall are not hoisted. `from embodiment import drone` then
    # `drone.create(...)`. Only the SHAPES a host composes against are here.
    # `invoke` RETURNS a record for a failed run rather than raising, which is
    # the seam the evocation audit trail is built on.
    #
    # `DRONES_ENABLED_BY_DEFAULT` and `DroneOptIn` ARE hoisted, unlike the
    # verbs: drones ship opt-in and off (claim c25), and a governance guard has
    # to be able to assert that from the package surface without running one.
    "Drone": "drone",
    "DroneAnswer": "drone",
    "DroneCall": "drone",
    "DroneError": "drone",
    "DroneOptIn": "drone",
    "DroneRecord": "drone",
    "DroneRequest": "drone",
    "Evocation": "drone",
    "SmokeResult": "drone",
    "SurfaceCheck": "drone",
    "SurfaceReport": "drone",
    "UndeclaredQuestion": "drone",
    "AskFn": "drone",
    "StatusFn": "drone",
    "DRONE_ENTRYPOINT": "drone",
    "DRONE_STATUSES": "drone",
    "DRONES_DIRNAME": "drone",
    "DRONES_ENABLED_BY_DEFAULT": "drone",
    "DRONES_ENABLED_ENV": "drone",
    "EVOCATION_OUTCOMES": "drone",
    "MANIFEST_SCHEMA_VERSION": "drone",
    "OPT_IN_OFF": "drone",
    "STATUS_BROKEN": "drone",
    "STATUS_OK": "drone",
    "STATUS_STALE": "drone",
    "STATUS_UNCHECKED": "drone",
    "STATUS_UNVERIFIABLE": "drone",
    # ── event emission (embodiment#4) — optional, absent by default ───────
    # embodiment produces; `events-cli` owns the envelope contract (c33).
    "EventEmitter": "events",
    "EventDegradation": "events",
    # ── one answer to "what went wrong?" ──────────────────────────────────
    # A reader over every lane's own degradation shape, not a replacement for
    # them: `read(loop=..., muse_runner=..., lifecycle=...)` folds six record
    # types into one stream and keeps each source record in `.original`.
    "LedgerRecord": "ledger",
    "read": "ledger",
    "known_codes": "ledger",
    "source_for_code": "ledger",
    # ── the lived sequence: when to consider, remember, revisit ───────────
    # `ContinuityLifecycle` IS a `ContinuityFn` — inject it as `continuity=`.
    # The host names which tools are consequential; embodiment cannot know
    # that a kiosk's `send_message` matters and its `get_weather` does not.
    "ContinuityLifecycle": "lifecycle",
    "LifecycleConfig": "lifecycle",
    "LifecycleEvent": "lifecycle",
    "LifecycleSink": "lifecycle",
    "ConsequentialFn": "lifecycle",
    "build_continuity_fn": "lifecycle",
    "select_for_memory": "lifecycle",
    "request_text": "lifecycle",
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
    # ── host-composable senses prompt text (task t11, issue #63) ─────────
    # TEXT, never framing: no function here builds a prompt or reaches a
    # senses seat, so shipping this constant does not cross the
    # embodiment-frames-cortex-not-senses boundary (README, colleague#352,
    # claim c30) the way a `frame_senses()` function would. A host composes
    # `SENSES_GROUNDING` into its OWN senses prompt; embodiment never does.
    # REQUIRED, not advisory — measured 0/16 vs 16/16, see the module.
    "SENSES_GROUNDING": "senses_text",
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
    # ── the recall bundle (raw memory material for the muse) ─────────────
    "fetch_bundle": "recall_bundle",
    "flat_fetch": "recall_bundle",
    "flat_fetcher": "recall_bundle",
    "RecallBundle": "recall_bundle",
    "BundleRequest": "recall_bundle",
    "BundleItem": "recall_bundle",
    "BundleProvenance": "recall_bundle",
    "BundleDegradation": "recall_bundle",
    "FetchFn": "recall_bundle",
    "RecallFn": "recall_bundle",
    "LEVEL_FLAT": "recall_bundle",
    "LEVEL_GRAPH": "recall_bundle",
    "graph_available": "recall_bundle",
    # ── the scratchpad (working memory a successor can resume from) ──────
    "Scratchpad": "scratchpad",
    "Entry": "scratchpad",
    "resume_report": "scratchpad",
    "PerceptionDegradation": "perception",
    # ── the subagent seam (delegation bounded by arithmetic) ──────────────
    "SubagentFn": "subagent",
    "SubagentCall": "subagent",
    "SubagentResult": "subagent",
    "SpawnRequest": "subagent",
    "SpawnRecord": "subagent",
    "NO_SPAWNS": "subagent",
    "SPAWN_GRANTED": "subagent",
    "SPAWN_REFUSED_ALLOWANCE": "subagent",
    "SPAWN_REFUSED_BUDGET": "subagent",
    "SPAWN_REFUSED_SEAM": "subagent",
    "SPAWN_FAILED": "subagent",
    "SPAWN_OUTCOMES": "subagent",
    "SPAWN_REFUSALS": "subagent",
}

__all__ = [
    "__version__",
    "ARCHIVED_SUBMODULES",
    *sorted(_SUBMODULES),
    *sorted(_LAZY_NAMES),
]


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
    elif name in _RESOLVABLE:
        # `_RESOLVABLE`, not `_SUBMODULES`: an ARCHIVED module resolves exactly
        # as a curated one does. Archival costs advertisement, never reach —
        # that is what keeps `embodiment.strategist_runner`'s citation openable.
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
    # `muse` and `muse_runner` are ARCHIVED (see `ARCHIVED_SUBMODULES`). They
    # are stubbed here anyway, and honestly: `__getattr__` resolves them at
    # runtime exactly as it resolves a curated submodule, so a consumer
    # type-checking against the archived lane should be able to see it. What
    # they are absent from is `__all__`, not the package.
    from embodiment import (  # noqa: F401
        capability,
        config_change,
        config_lifecycle,
        context,
        continuity,
        contract,
        drone,
        events,
        framing,
        identity,
        ledger,
        lifecycle,
        loop,
        media,
        muse,
        muse_pad,
        muse_runner,
        perception,
        presence,
        presence_engine,
        recall_bundle,
        scope,
        scoped_run,
        scratchpad,
        senses_text,
        strategist_runner,
        subagent,
        workspace,
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
    from embodiment.drone import (  # noqa: F401
        DRONE_ENTRYPOINT,
        DRONE_STATUSES,
        DRONES_DIRNAME,
        DRONES_ENABLED_BY_DEFAULT,
        DRONES_ENABLED_ENV,
        EVOCATION_OUTCOMES,
        MANIFEST_SCHEMA_VERSION,
        OPT_IN_OFF,
        STATUS_BROKEN,
        STATUS_OK,
        STATUS_STALE,
        STATUS_UNCHECKED,
        STATUS_UNVERIFIABLE,
        AskFn,
        Drone,
        DroneAnswer,
        DroneCall,
        DroneError,
        DroneOptIn,
        DroneRecord,
        DroneRequest,
        Evocation,
        SmokeResult,
        StatusFn,
        SurfaceCheck,
        SurfaceReport,
        UndeclaredQuestion,
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
    from embodiment.ledger import (  # noqa: F401
        LedgerRecord,
        known_codes,
        read,
        source_for_code,
    )
    from embodiment.lifecycle import (  # noqa: F401
        ConsequentialFn,
        ContinuityLifecycle,
        LifecycleConfig,
        LifecycleEvent,
        LifecycleSink,
        build_continuity_fn,
        request_text,
        select_for_memory,
    )
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
    from embodiment.muse_pad import (  # noqa: F401
        MUSE_PAD_PROTOCOL,
        MUSE_PAD_TOOLS,
        MusePad,
        MusePadCounts,
    )
    from embodiment.perception import PerceptionDegradation  # noqa: F401
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
    from embodiment.recall_bundle import (  # noqa: F401
        LEVEL_FLAT,
        LEVEL_GRAPH,
        BundleDegradation,
        BundleItem,
        BundleProvenance,
        BundleRequest,
        FetchFn,
        RecallBundle,
        RecallFn,
        fetch_bundle,
        flat_fetch,
        flat_fetcher,
        graph_available,
    )
    from embodiment.scope import (  # noqa: F401
        FORBIDDEN_DIRECTIVE_KEYS,
        LANE_DURABLE,
        LANE_SESSION,
        MARKER_DIRECTIVE,
        MARKER_HOLD,
        REFUSAL_CODES,
        SCOPE_AUTHORITY,
        SCOPE_LANES,
        ScopeCompleteFn,
        ScopeControls,
        ScopeDegradation,
        ScopeDirective,
        ScopeLoop,
        ScopeOutcome,
        ScopeRegister,
        ScopeRejection,
        ScopeReport,
        ScopeResponsibility,
        ScopeSnapshot,
        ScopeToolBench,
        ScopeToolCompleteFn,
        ScopeToolExecuteFn,
        directive_from_payload,
    )
    from embodiment.scoped_run import (  # noqa: F401
        ScopeContext,
        ScopedControls,
        ScopedOutcome,
        ScopeGovernor,
        ScopePersistence,
        ScopeProjectorFn,
        ScopeSession,
        ScopeTransition,
        render_directive,
        run_scoped,
    )
    from embodiment.scratchpad import (  # noqa: F401
        Entry,
        Scratchpad,
        resume_report,
    )
    from embodiment.senses_text import SENSES_GROUNDING  # noqa: F401
    from embodiment.strategist_runner import (  # noqa: F401
        STRATEGIST_ROLE,
        StrategistLimits,
        StrategistRunner,
    )
    from embodiment.subagent import (  # noqa: F401
        NO_SPAWNS,
        SPAWN_FAILED,
        SPAWN_GRANTED,
        SPAWN_OUTCOMES,
        SPAWN_REFUSALS,
        SPAWN_REFUSED_ALLOWANCE,
        SPAWN_REFUSED_BUDGET,
        SPAWN_REFUSED_SEAM,
        SpawnRecord,
        SpawnRequest,
        SubagentCall,
        SubagentFn,
        SubagentResult,
    )
    from embodiment.workspace import (  # noqa: F401
        WORKSPACE_PROTOCOL,
        WORKSPACE_TOOLS,
        MuseWorkspace,
        WorkspaceCounts,
        WorkspaceDegradation,
    )
