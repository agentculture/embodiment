"""The tool registry — a :class:`~embodiment.loop.ToolExecutor` that starts empty.

One spoken turn is driven through :func:`embodiment.loop.run`, and ``run``
takes an *injected* executor: the loop never constructs one and never
enumerates tools. This module is the smallest honest executor a host can hand
it — a registry of ``(name, JSON schema, callable)`` triples — and its default
state is **zero tools**.

Why it exists before there is anything to register
--------------------------------------------------
Gwen is meant to grow tool use and agent triggering as *additions*, never as a
rewrite. The way to guarantee that is to build the turn on the bounded tool
loop from day one with a registry that happens to be empty: registering a tool
changes what the registry holds, not the code path the turn takes. The registry
is the seam that makes the growth additive, so it ships with the turn rather
than after it.

Empty means empty, on the wire too
-----------------------------------
:meth:`ToolRegistry.wire_tools` returns ``None`` — not ``[]`` — while nothing
is registered, which is the "omit the ``tools`` field entirely" signal an
OpenAI-compatible request needs. :func:`bind_tools` is the one place that
reading is applied, so a seam built through it puts **no tool schema on the
wire** for an empty registry. A model that is never shown a tool cannot be
blamed for not calling one, and a voice turn that never advertises a tool is
measurably the cheapest turn available.

What the registry does with a failing tool
-------------------------------------------
A tool callable that raises is converted to :class:`~embodiment.loop.ToolError`
— the executor's own contract, which the loop contains as ONE self-correcting
step rather than an abort — **and** recorded on this registry's own
:attr:`ToolRegistry.degradations` ledger. Both, not either: the loop's
containment keeps the turn bounded, and the ledger is what makes the failure
host-visible (constraint C3). A call naming a tool that was never registered
raises :class:`~embodiment.loop.UnknownToolError` and is recorded the same way.

This module holds no policy about *which* tools may exist, no shell, no
filesystem and no network. Authority over a tool belongs to whoever registered
it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from embodiment.loop import ToolError, ToolOutcome, UnknownToolError

__all__ = [
    "BOUND_REGISTRY_ATTR",
    "DEGRADED_TOOL_FAILED",
    "DEGRADED_TOOL_UNKNOWN",
    "ToolFn",
    "ToolSpec",
    "ToolDegradation",
    "ToolRegistry",
    "bind_tools",
]

#: A registered tool's callable: arguments in, anything ``str()``-able out.
ToolFn = Callable[..., Any]

#: Attribute name :func:`bind_tools` stamps on the callable it returns.
BOUND_REGISTRY_ATTR = "__embodiment_bound_registry__"

#: A registered tool raised.
DEGRADED_TOOL_FAILED = "tool-failed"
#: The model named a tool nobody registered.
DEGRADED_TOOL_UNKNOWN = "tool-unknown"

#: Cap on one degradation's reason text, as every sibling lane caps its own.
_MAX_REASON_LEN = 500


@dataclass(frozen=True)
class ToolDegradation:
    """One recorded, host-visible tool degradation (constraint C3).

    Field-for-field a prefix of :class:`embodiment.loop.LoopDegradation` and
    :class:`embodiment.perception.PerceptionDegradation`, so a host folds one
    shape rather than yet another.
    """

    code: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "reason": self.reason}


@dataclass(frozen=True)
class ToolSpec:
    """One registered tool: what it is called, what it takes, what it runs.

    Args:
        name: the wire name the model calls.
        parameters: the JSON schema for the tool's arguments, verbatim. This
            module never validates against it and never rewrites it — it is the
            host's declaration, passed through to the wire unchanged.
        fn: the callable. Invoked as ``fn(**arguments)``; a tool declaring a
            single ``arguments`` parameter is not special-cased, because the
            schema already says what the model may send.
        description: the one-line description shown to the model.
        finishes: whether a successful call ends the turn. ``False`` (the
            default) feeds the result back and lets the model speak after it.
    """

    name: str
    parameters: dict[str, Any]
    fn: ToolFn
    description: str = ""
    finishes: bool = False

    def schema(self) -> dict[str, Any]:
        """This tool as one OpenAI-compatible ``tools`` entry."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": dict(self.parameters),
            },
        }


@dataclass
class ToolRegistry:
    """A :class:`~embodiment.loop.ToolExecutor` holding zero tools by default.

    Structurally a valid executor: it has an ``execute(name, arguments)``
    returning a :class:`~embodiment.loop.ToolOutcome`, which is the entire
    protocol :func:`embodiment.loop.run` requires.
    """

    specs: dict[str, ToolSpec] = field(default_factory=dict)
    degradations: list[ToolDegradation] = field(default_factory=list)

    # ── registration ──────────────────────────────────────────────────────

    def register(
        self,
        name: str,
        parameters: dict[str, Any],
        fn: ToolFn,
        *,
        description: str = "",
        finishes: bool = False,
    ) -> ToolSpec:
        """Register one tool and return its :class:`ToolSpec`.

        Raises:
            ValueError: on a blank name or a name already registered. A silently
                replaced tool is a tool surface that changed without anyone
                deciding, which is the opposite of what this registry is for.
        """
        clean = (name or "").strip()
        if not clean:
            raise ValueError("a tool needs a name")
        if clean in self.specs:
            raise ValueError(f"tool already registered: {clean}")
        spec = ToolSpec(
            name=clean,
            parameters=dict(parameters or {}),
            fn=fn,
            description=description,
            finishes=finishes,
        )
        self.specs[clean] = spec
        return spec

    def add(self, spec: ToolSpec) -> ToolSpec:
        """Register an already-built :class:`ToolSpec`."""
        return self.register(
            spec.name,
            spec.parameters,
            spec.fn,
            description=spec.description,
            finishes=spec.finishes,
        )

    # ── introspection ─────────────────────────────────────────────────────

    @property
    def empty(self) -> bool:
        """True while nothing is registered — the shipped default."""
        return not self.specs

    def names(self) -> tuple[str, ...]:
        """Every registered name, in registration order."""
        return tuple(self.specs)

    def schemas(self) -> list[dict[str, Any]]:
        """Every tool as an OpenAI-compatible entry; ``[]`` while empty."""
        return [spec.schema() for spec in self.specs.values()]

    def wire_tools(self) -> Optional[list[dict[str, Any]]]:
        """The ``tools`` request field: ``None`` while empty, else the schemas.

        ``None`` rather than ``[]`` on purpose. An empty list is still a
        ``tools`` field on the wire; ``None`` is the signal to omit it, which is
        what "no tool schema is put on the wire" has to mean.
        """
        return self.schemas() or None

    def __len__(self) -> int:
        return len(self.specs)

    def __contains__(self, name: object) -> bool:
        return name in self.specs

    # ── the executor protocol ─────────────────────────────────────────────

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        """Run one registered tool. The loop's whole executor contract.

        Raises:
            UnknownToolError: the model named a tool nobody registered.
            ToolError: the tool raised. Both are recorded on
                :attr:`degradations` before they leave, and both cost the loop
                exactly one self-correcting step rather than the turn.
        """
        spec = self.specs.get(name)
        if spec is None:
            self._degrade(DEGRADED_TOOL_UNKNOWN, f"{name}: not registered")
            raise UnknownToolError(f"unknown tool: {name}")
        try:
            value = spec.fn(**dict(arguments or {}))
        except Exception as exc:  # noqa: BLE001  # recorded below, then re-raised as ToolError
            # Broad on purpose: a registered tool is host code this module knows
            # nothing about. It is neither swallowed nor allowed to abort the
            # turn — the degradation is recorded first (C3), then the failure is
            # re-shaped into the executor contract the loop contains.
            self._degrade(DEGRADED_TOOL_FAILED, f"{name}: {type(exc).__name__}: {exc}")
            raise ToolError(f"{name} failed: {type(exc).__name__}: {exc}") from exc
        result = "" if value is None else str(value)
        return ToolOutcome(
            result=result,
            finished=spec.finishes,
            finish_summary=result if spec.finishes else "",
        )

    # ── internals ─────────────────────────────────────────────────────────

    def _degrade(self, code: str, reason: str) -> None:
        self.degradations.append(ToolDegradation(code=code, reason=reason[:_MAX_REASON_LEN]))


def bind_tools(
    seam: Callable[..., Any],
    registry: Optional[ToolRegistry] = None,
) -> Callable[[list[dict[str, Any]]], Any]:
    """Close a schema-aware *seam* over *registry*, yielding a ``CompleteFn``.

    :data:`embodiment.loop.CompleteFn` takes messages and nothing else, so the
    tool schemas have to be bound in before the loop ever sees the callable.
    This is the one place that binding happens.

    *seam* is called as ``seam(messages, tools=<wire tools>)``, where the wire
    tools are ``None`` for an empty or absent registry — so a turn with nothing
    registered advertises no tool at all.

    The returned callable is **marked** with the registry it closed over, under
    :data:`BOUND_REGISTRY_ATTR`. Forgetting this call is the first mistake a
    tool author makes — registered tools that never reach the wire produce a
    presence that silently has no tools — and the marker is what lets
    :func:`embodiment.turn.turn` notice and record it.
    """
    reg = registry if registry is not None else ToolRegistry()

    def complete(messages: list[dict[str, Any]]) -> Any:
        return seam(messages, tools=reg.wire_tools())

    setattr(complete, BOUND_REGISTRY_ATTR, reg)
    return complete
