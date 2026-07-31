#!/usr/bin/env python3
"""arch_arms — four architectures over the challenge rungs, metered per call.

Plan task **t5** of `orchestrator-worker-architectures`
(`docs/plans/2026-07-31-orchestrator-worker-architectures.md`). This is the
series' core instrument: the thing that actually runs an architecture against a
problem and writes down what it cost. It **composes** the two tasks that landed
before it and reimplements neither — ``examples/orchestrator_tools.py`` (t1)
owns the delegate tool and the subagent seam, ``examples/worker_seam.py`` (t2)
owns the dial, the no-silent-fallback rule and the transport.

The four arms
-------------

=====  ==================  ================================================
arm    shape               who acts
=====  ==================  ================================================
``E``  flat                the cortex alone — today's rig, **re-measured**
``W``  flat                the worker alone — the control
``M``  orchestrated        cortex orchestrates; the worker executes all
                           ground work
``H``  orchestrated        cortex routes the work it judges simple to the
                           worker and keeps the rest
=====  ==================  ================================================

**Arm W is not decoration.** Without a flat-worker cell, a manager or hybrid
win cannot be attributed to *orchestration* rather than to the worker simply
being sufficient on its own. Every prior series in this repo taught the same
lesson from the other direction — the recurring correction in
``docs/live-test-results/corrections.md`` is a missing control — so
:func:`analyse` **refuses to emit a verdict** for any rung that has data but is
missing either flat arm's cell, and reports missing cells ``ABSENT``.

An arm is *data*, never a code branch: :data:`ARMS` describes the four shapes
and :func:`run_attempt` reads them. ``M`` and ``H`` differ by exactly one field
(``keeps_work``), which is what puts the problem's own tool bench on the hybrid
orchestrator's surface and withholds it from the manager's.

Arm E is not "the rig we already measured"
------------------------------------------
The rig's cortex is being upgraded to a vision-capable model as this lands.
``docs/live-test-results/arena-budget.md``, ``league-h2h.md`` and
``league-commander.md`` describe a cortex that will no longer exist; they
inform this design and are **never** cited as this series' baseline. Arm E is
re-measured against whatever id is dialled. Accordingly **no model id appears
in this file** — every identity, endpoint and sampling parameter is read from
:data:`DEFAULT_CONFIG_PATH`, and an unresolvable identity is a recorded
``ABSENT`` arm rather than a substituted predecessor.

The sampling table lives in a committed file, not in this module
-----------------------------------------------------------------
``docs/live-test-results/configurations.md`` records temperature as a hidden
variable that confounded an entire prior series: four harnesses hard-coded four
values and one was chosen by vibe ("designing is creative, therefore 0.8"). So
temperature, thinking mode and ``max_tokens`` — per role **per arm** — are read
from ``docs/live-test-results/arch-arms-sampling.json``, and this module defines
no fallback for any of them: a missing cell raises :class:`ConfigError` naming
the arm and the role. ``tests/test_arch_arms.py`` asserts that structurally over
this file's own AST, so a default cannot be reintroduced quietly.

What goes on the wire for "thinking: on" is in that file too. The convention
differs by server and model family, and the upgraded cortex's is not yet known;
a harness that hard-coded one would be inventing a fact about a model nobody has
dialled.

Senses is hashed, not trusted
-----------------------------
Senses configuration must be byte-identical across arms — a senses difference
would confound every arm at once. :func:`assert_senses_identical` refuses to run
a series whose arms disagree, and every per-call record carries the hash, so the
invariant is checkable from a committed artifact alone. Senses is **not dialled**
by these text-only rungs; it is recorded, the way `league_h2h` records
``SENSES_MODEL`` for a seat with no senses lane. The hash starts biting on real
traffic when the perception-routing rung dials it.

Per-call records, because ``ModelResponse`` cannot carry them
--------------------------------------------------------------
``embodiment.contract.ModelResponse`` deliberately carries no ``finish_reason``
(issue #37), so a truncated turn and a deliberate one arrive at the loop as the
same object — which is how t24 measured the shipped 2048 default truncating
6.0% of completions with **zero** degradations recorded. Every harness here
therefore builds its own record off the raw response, and so does this one:
:class:`CallRecord` carries ``finish_reason``, prompt / completion /
**reasoning** tokens *separately*, role, model and arm. Reasoning is separate
because both acting minds are thinking models and reasoning spend dominates;
where the server reports no reasoning breakdown the field is ``None`` and the
source says ``absent`` — never ``0``, which would read as a thinking model that
thought about nothing.

Perception routing is not built here, and not precluded
--------------------------------------------------------
A later task adds a routing dimension (an image reaching the deciding mind
natively, as a senses description, or both). This module builds none of it. It
does key every cell by ``(rung, arm, route)`` with exactly one route today
(:data:`ROUTE_TEXT`), because a two-part key would force that task to rewrite
this one's :func:`analyse`.

No live dial happens here
-------------------------
The whole harness is exercisable hermetically with scripted minds, and the live
lane is gated on ``EMBODIMENT_LIVE_RIG=1`` (:func:`require_live_rig`). Running
the series live is a later task and the operator has frozen live runs.

Usage::

    uv run python examples/arch_arms.py plan
    uv run python examples/arch_arms.py config --json
    uv run python examples/arch_arms.py run --rung C1 --out /tmp/arch.jsonl
    uv run python examples/arch_arms.py analyse --log /tmp/arch.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from embodiment import (  # noqa: E402
    LoopAborted,
    ModelResponse,
    Task,
    ToolError,
    ToolOutcome,
    UnknownToolError,
    ledger,
    run,
)
from embodiment.contract import ToolCall  # noqa: E402
from examples import challenge_entropic, challenge_register, challenge_subset  # noqa: E402
from examples import orchestrator_tools as ot  # noqa: E402
from examples import worker_seam as ws  # noqa: E402

__all__ = [
    "ARMS",
    "ARM_EXISTING",
    "ARM_HYBRID",
    "ARM_MANAGER",
    "ARM_ORDER",
    "ARM_WORKER_SOLO",
    "Arm",
    "ArchSeam",
    "AttemptRecord",
    "BenchWorkerExecutor",
    "Budget",
    "CallContext",
    "CallLog",
    "CallRecord",
    "CellResult",
    "ConfigError",
    "DEFAULT_CONFIG_PATH",
    "DIFFICULTIES",
    "Dial",
    "DialResolution",
    "FLAT_ARMS",
    "LADDER",
    "LIVE_GATE_ENV",
    "LiveRigClosed",
    "LiveSeams",
    "PROBLEMS",
    "Problem",
    "ROUTES",
    "ROUTE_TEXT",
    "Rung",
    "Sampling",
    "ScriptedArchSeam",
    "ScriptedSeams",
    "ArchConfig",
    "ArchOrchestrator",
    "RoleDial",
    "analyse",
    "assert_senses_identical",
    "load_config",
    "main",
    "orchestration_schema",
    "require_live_rig",
    "resolve_dial",
    "run_attempt",
    "run_series",
    "top_level_prompt",
    "worker_schema_for",
    "worker_tools_for",
]

# ── where the configuration lives ────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The committed sampling table. An INPUT, unlike every other ``*.json`` in that
#: directory — see the file's own ``why`` block.
DEFAULT_CONFIG_PATH = REPO_ROOT / "docs" / "live-test-results" / "arch-arms-sampling.json"

#: The repo-wide live gate. Every live-capable module here reads the same one.
LIVE_GATE_ENV = "EMBODIMENT_LIVE_RIG"


class ConfigError(ValueError):
    """The committed configuration is missing something this harness needs.

    Always names the arm and role at fault: a config error that says only
    "invalid config" sends the reader back to a 200-line JSON file.
    """


class LiveRigClosed(RuntimeError):
    """A live dial was requested without the gate open."""


def require_live_rig(env: Optional[Mapping[str, str]] = None) -> None:
    """Refuse a live dial unless ``EMBODIMENT_LIVE_RIG=1``.

    Live runs are frozen for this cycle. The gate lives in the harness rather
    than only in the test suite, so a hand-run command is refused too.
    """
    live_env: Mapping[str, str] = os.environ if env is None else env
    if live_env.get(LIVE_GATE_ENV) != "1":
        raise LiveRigClosed(
            f"live dialling needs {LIVE_GATE_ENV}=1 in the environment; "
            "the scripted lane needs nothing"
        )


# ── roles, arms, routes ──────────────────────────────────────────────────────

ROLE_CORTEX = "cortex"
ROLE_WORKER = "worker"
ROLE_SENSES = "senses"
ROLES = (ROLE_CORTEX, ROLE_WORKER, ROLE_SENSES)

SHAPE_FLAT = "flat"
SHAPE_ORCHESTRATED = "orchestrated"

ARM_EXISTING = "E"
ARM_WORKER_SOLO = "W"
ARM_MANAGER = "M"
ARM_HYBRID = "H"


@dataclass(frozen=True)
class Arm:
    """One architecture, as data. Nothing here branches on an arm id."""

    id: str
    label: str
    shape: str
    #: The role driving the top-level acting loop. It holds final authority in
    #: every arm — in ``W`` that IS the worker, and it is framed as such.
    top_level_role: str
    #: Roles that actually get dialled in this arm.
    acting_roles: tuple[str, ...]
    #: Roles that must have a sampling cell. Wider than ``acting_roles``:
    #: senses is configured (and hashed) in every arm without being dialled.
    configured_roles: tuple[str, ...]
    delegates: bool
    #: Whether the top-level mind holds the problem's own tool bench. The one
    #: field separating the manager from the hybrid.
    keeps_work: bool
    why: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "shape": self.shape,
            "top_level_role": self.top_level_role,
            "acting_roles": list(self.acting_roles),
            "configured_roles": list(self.configured_roles),
            "delegates": self.delegates,
            "keeps_work": self.keeps_work,
            "why": self.why,
        }


ARMS: dict[str, Arm] = {
    ARM_EXISTING: Arm(
        id=ARM_EXISTING,
        label="existing",
        shape=SHAPE_FLAT,
        top_level_role=ROLE_CORTEX,
        acting_roles=(ROLE_CORTEX,),
        configured_roles=(ROLE_CORTEX, ROLE_SENSES),
        delegates=False,
        keeps_work=True,
        why=(
            "today's single-actor rig (d15), RE-MEASURED against the upgraded "
            "cortex — prior results describe a mind that no longer exists"
        ),
    ),
    ARM_WORKER_SOLO: Arm(
        id=ARM_WORKER_SOLO,
        label="worker-solo",
        shape=SHAPE_FLAT,
        top_level_role=ROLE_WORKER,
        acting_roles=(ROLE_WORKER,),
        configured_roles=(ROLE_WORKER, ROLE_SENSES),
        delegates=False,
        keeps_work=True,
        why=(
            "the control without which no orchestration claim is attributable: "
            "a manager or hybrid win could otherwise be the worker being enough"
        ),
    ),
    ARM_MANAGER: Arm(
        id=ARM_MANAGER,
        label="manager",
        shape=SHAPE_ORCHESTRATED,
        top_level_role=ROLE_CORTEX,
        acting_roles=(ROLE_CORTEX, ROLE_WORKER),
        configured_roles=(ROLE_CORTEX, ROLE_WORKER, ROLE_SENSES),
        delegates=True,
        keeps_work=False,
        why="the cortex orchestrates and the worker executes ALL ground work",
    ),
    ARM_HYBRID: Arm(
        id=ARM_HYBRID,
        label="hybrid",
        shape=SHAPE_ORCHESTRATED,
        top_level_role=ROLE_CORTEX,
        acting_roles=(ROLE_CORTEX, ROLE_WORKER),
        configured_roles=(ROLE_CORTEX, ROLE_WORKER, ROLE_SENSES),
        delegates=True,
        keeps_work=True,
        why="the cortex routes what it judges simple to the worker and keeps the rest",
    ),
}

#: Presentation order, and the order :func:`analyse` reports cells in.
ARM_ORDER: tuple[str, ...] = (ARM_EXISTING, ARM_WORKER_SOLO, ARM_MANAGER, ARM_HYBRID)

#: The two arms a verdict cannot be stated without.
FLAT_ARMS: tuple[str, ...] = (ARM_EXISTING, ARM_WORKER_SOLO)

#: The only perception route this task ships. A later task adds the others; the
#: cell key already carries the dimension so it can add rows, not rewrite
#: :func:`analyse`.
ROUTE_TEXT = "text"
ROUTES: tuple[str, ...] = (ROUTE_TEXT,)
ROUTE_WHY: dict[str, str] = {
    ROUTE_TEXT: (
        "no image reaches any mind: the challenge rungs are text-only, so every "
        "arm sees identical content and only the architecture varies"
    )
}


# ── the committed configuration ──────────────────────────────────────────────


def _required(raw: Mapping[str, Any], key: str, where: str) -> Any:
    """Read a required key. There is no default — that is the whole point."""
    if not isinstance(raw, Mapping) or key not in raw:
        raise ConfigError(
            f"{where}: missing required key {key!r}. The sampling table is the "
            "only source for it; this harness carries no default to fall back on."
        )
    return raw[key]


@dataclass(frozen=True)
class Sampling:
    """One role's sampling, in one arm. Every field comes from the file."""

    temperature: float
    thinking: str
    max_tokens: int

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], *, where: str) -> "Sampling":
        return cls(
            temperature=float(_required(raw, "temperature", where)),
            thinking=str(_required(raw, "thinking", where)),
            max_tokens=int(_required(raw, "max_tokens", where)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "temperature": self.temperature,
            "thinking": self.thinking,
            "max_tokens": self.max_tokens,
        }


@dataclass(frozen=True)
class Budget:
    """The drive budgets for one arm. Sufficient, not equal — see the file."""

    max_steps: int
    worker_max_steps: int
    spawn_allowance: int

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], *, where: str) -> "Budget":
        return cls(
            max_steps=int(_required(raw, "max_steps", where)),
            worker_max_steps=int(_required(raw, "worker_max_steps", where)),
            spawn_allowance=int(_required(raw, "spawn_allowance", where)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_steps": self.max_steps,
            "worker_max_steps": self.worker_max_steps,
            "spawn_allowance": self.spawn_allowance,
        }


@dataclass(frozen=True)
class RoleDial:
    """Where one role lives. ``model`` may be ``None`` — see the cortex entry."""

    role: str
    model: Optional[str]
    model_env: str
    base_url: Optional[str]
    base_url_env: str
    api_key_env: str

    @classmethod
    def from_dict(cls, role: str, raw: Mapping[str, Any]) -> "RoleDial":
        where = f"roles.{role}"
        model = raw.get("model")
        base_url = raw.get("base_url")
        return cls(
            role=role,
            model=str(model) if model else None,
            model_env=str(_required(raw, "model_env", where)),
            base_url=str(base_url) if base_url else None,
            base_url_env=str(_required(raw, "base_url_env", where)),
            api_key_env=str(_required(raw, "api_key_env", where)),
        )

    def to_dict(self) -> dict[str, Any]:
        # No key material passes through here; this object never holds one.
        return {
            "role": self.role,
            "model": self.model,
            "model_env": self.model_env,
            "base_url": self.base_url,
            "base_url_env": self.base_url_env,
            "api_key_env": self.api_key_env,
        }


@dataclass(frozen=True)
class ArchConfig:
    """The whole committed configuration, parsed and validated once."""

    path: Optional[Path]
    version: int
    roles: Mapping[str, RoleDial]
    thinking_modes: Mapping[str, Mapping[str, Any]]
    sampling: Mapping[str, Mapping[str, Sampling]]
    budgets: Mapping[str, Budget]
    decision: Mapping[str, Any]

    def role(self, name: str) -> RoleDial:
        if name not in self.roles:
            raise ConfigError(f"roles: no entry for {name!r}; known roles are {sorted(self.roles)}")
        return self.roles[name]

    def sampling_for(self, arm: str, role: str) -> Sampling:
        if arm not in self.sampling:
            raise ConfigError(f"sampling: no cell for arm {arm!r}")
        cells = self.sampling[arm]
        if role not in cells:
            raise ConfigError(
                f"sampling.{arm}: no cell for role {role!r}. Every role an arm "
                "configures needs its own temperature, thinking mode and max_tokens."
            )
        return cells[role]

    def budget_for(self, arm: str) -> Budget:
        if arm not in self.budgets:
            raise ConfigError(f"budgets: no entry for arm {arm!r}")
        return self.budgets[arm]

    def wire_extra(self, thinking: str) -> dict[str, Any]:
        """The body keys this thinking mode puts on the wire, from the file.

        An unmapped mode raises rather than sending nothing: a silent no-op
        would mean the recorded mode and the dialled mode disagree, which is
        exactly the class of hidden variable this configuration exists to stop.
        An entry of ``{}`` is an explicit "send no extra keys".
        """
        if thinking not in self.thinking_modes:
            raise ConfigError(
                f"thinking_wire.modes: no entry for mode {thinking!r}; "
                f"known modes are {sorted(self.thinking_modes)}"
            )
        return dict(self.thinking_modes[thinking])

    def senses_hashes(self) -> dict[str, str]:
        """Each arm's senses configuration, as a hash. One per arm, always."""
        dial = self.role(ROLE_SENSES).to_dict()
        out: dict[str, str] = {}
        for arm in ARM_ORDER:
            payload = {"role": dial, "sampling": self.sampling_for(arm, ROLE_SENSES).to_dict()}
            canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            out[arm] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path) if self.path else None,
            "version": self.version,
            "roles": {name: dial.to_dict() for name, dial in self.roles.items()},
            "thinking_wire": {mode: dict(keys) for mode, keys in self.thinking_modes.items()},
            "sampling": {
                arm: {role: cell.to_dict() for role, cell in cells.items()}
                for arm, cells in self.sampling.items()
            },
            "budgets": {arm: budget.to_dict() for arm, budget in self.budgets.items()},
            "decision": dict(self.decision),
        }


def load_config(path: Optional[Path] = None) -> ArchConfig:
    """Read and validate the committed sampling table.

    Validation is eager and total: every arm's every configured role is parsed
    at load, so a missing ``temperature`` fails before the first dial rather
    than three hours into a series.
    """
    resolved = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except FileNotFoundError as missing:
        raise ConfigError(f"no sampling table at {resolved}") from missing
    except ValueError as broken:
        raise ConfigError(f"{resolved} is not readable JSON: {broken}") from broken
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{resolved}: the sampling table must be a JSON object")

    roles = {
        name: RoleDial.from_dict(name, entry)
        for name, entry in (_required(raw, "roles", str(resolved))).items()
    }
    for name in ROLES:
        if name not in roles:
            raise ConfigError(f"{resolved}: roles is missing {name!r}")

    wire = _required(raw, "thinking_wire", str(resolved))
    modes = {mode: dict(keys) for mode, keys in (_required(wire, "modes", "thinking_wire")).items()}

    sampling_raw = _required(raw, "sampling", str(resolved))
    sampling: dict[str, dict[str, Sampling]] = {}
    for arm in ARM_ORDER:
        if arm not in sampling_raw:
            raise ConfigError(f"sampling: no cell for arm {arm!r}")
        cells: dict[str, Sampling] = {}
        for role in ARMS[arm].configured_roles:
            if role not in sampling_raw[arm]:
                raise ConfigError(f"sampling.{arm}: no cell for role {role!r}")
            cell = Sampling.from_dict(sampling_raw[arm][role], where=f"sampling.{arm}.{role}")
            if cell.thinking not in modes:
                raise ConfigError(
                    f"sampling.{arm}.{role}: thinking mode {cell.thinking!r} has no "
                    f"thinking_wire.modes entry; known modes are {sorted(modes)}"
                )
            cells[role] = cell
        sampling[arm] = cells

    budgets_raw = _required(raw, "budgets", str(resolved))
    budgets = {
        arm: Budget.from_dict(
            _required(budgets_raw, arm, "budgets"),
            where=f"budgets.{arm}",
        )
        for arm in ARM_ORDER
    }

    return ArchConfig(
        path=resolved,
        version=int(raw.get("version") or 0),
        roles=roles,
        thinking_modes=modes,
        sampling=sampling,
        budgets=budgets,
        decision=dict(_required(raw, "decision", str(resolved))),
    )


def assert_senses_identical(config: ArchConfig) -> str:
    """Refuse a series whose arms disagree about senses. Returns the one hash.

    Senses is the shared substrate of every arm. If one arm's senses differs,
    the whole comparison measures senses as well as architecture — and it would
    do so invisibly, because senses is not what anyone would be looking at.
    """
    hashes = config.senses_hashes()
    distinct = sorted(set(hashes.values()))
    if len(distinct) != 1:
        grouped: dict[str, list[str]] = {}
        for arm, digest in hashes.items():
            grouped.setdefault(digest, []).append(arm)
        detail = "; ".join(
            f"{digest[:12]}… = arms {sorted(arms)}" for digest, arms in sorted(grouped.items())
        )
        raise ConfigError(
            "senses configuration differs between arms and would confound every "
            f"one of them: {detail}. Check sampling.<arm>.senses and roles.senses."
        )
    return distinct[0]


# ── resolving a dial: config, then env, then nothing ─────────────────────────

DEGRADED_MODEL_ABSENT = "arch-model-absent"
DEGRADED_BASE_URL_ABSENT = "arch-base-url-absent"
DEGRADED_API_KEY_ABSENT = "arch-api-key-absent"
DEGRADED_BASE_URL_INVALID = "arch-base-url-invalid"


@dataclass(frozen=True)
class Dial:
    """A fully-resolved endpoint for one role."""

    role: str
    model: str
    base_url: str
    api_key: str

    def to_dict(self) -> dict[str, Any]:
        # The key is never serialized, echoed or logged (t2's convention).
        return {"role": self.role, "model": self.model, "base_url": self.base_url}


@dataclass(frozen=True)
class DialResolution:
    """Either a usable dial, or a recorded ``ABSENT`` — never a substitution."""

    role: str
    dial: Optional[Dial]
    degradations: tuple[ws.WorkerDegradation, ...] = ()

    @property
    def ok(self) -> bool:
        return self.dial is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "ok": self.ok,
            "dial": self.dial.to_dict() if self.dial else None,
            "degradations": [record.to_dict() for record in self.degradations],
        }


def resolve_dial(
    config: ArchConfig,
    role: str,
    *,
    env: Optional[Mapping[str, str]] = None,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
) -> DialResolution:
    """Resolve one role's endpoint: explicit flag, then env, then the file.

    Env outranks the committed file so an operator can dial a freshly-served
    model without editing a record of what a *past* run used; the flag outranks
    both. There is no fourth source. Absence of any input is a recorded
    :class:`~examples.worker_seam.WorkerDegradation` and ``dial=None`` — the
    arm is reported ``ABSENT``, never quietly pointed at some other model.

    The worker's resolution goes through
    :func:`examples.worker_seam.resolve_worker_config` rather than a parallel
    copy: t2 owns the guarantee that a worker dial never falls back to the
    spark gateway, and a second implementation of that rule is a second place
    for it to rot.
    """
    live_env: Mapping[str, str] = os.environ if env is None else env
    dialled = config.role(role)
    effective_model = (model or live_env.get(dialled.model_env) or dialled.model or "").strip()
    effective_url = (
        base_url or live_env.get(dialled.base_url_env) or dialled.base_url or ""
    ).strip()

    if role == ROLE_WORKER:
        resolution = ws.resolve_worker_config(
            cli_url=effective_url or None,
            cli_model=effective_model or None,
            env=live_env,
        )
        if resolution.config is None:
            return DialResolution(role=role, dial=None, degradations=resolution.degradations)
        return DialResolution(
            role=role,
            dial=Dial(
                role=role,
                model=resolution.config.model,
                base_url=resolution.config.base_url,
                api_key=resolution.config.api_key,
            ),
        )

    api_key = live_env.get(dialled.api_key_env, "").strip()
    degradations: list[ws.WorkerDegradation] = []
    if not effective_model:
        degradations.append(
            ws.WorkerDegradation(
                DEGRADED_MODEL_ABSENT,
                f"no --{role}-model and no {dialled.model_env}, and the sampling "
                f"table pins no model for {role!r} -- refusing to substitute one",
            )
        )
    if not effective_url:
        degradations.append(
            ws.WorkerDegradation(
                DEGRADED_BASE_URL_ABSENT,
                f"no --{role}-url and no {dialled.base_url_env}, and the sampling "
                f"table pins no endpoint for {role!r}",
            )
        )
    if not api_key:
        degradations.append(
            ws.WorkerDegradation(
                DEGRADED_API_KEY_ABSENT, f"no {dialled.api_key_env} in the environment"
            )
        )
    if degradations:
        return DialResolution(role=role, dial=None, degradations=tuple(degradations))

    if not effective_url.startswith(("http://", "https://")):
        return DialResolution(
            role=role,
            dial=None,
            degradations=(
                ws.WorkerDegradation(DEGRADED_BASE_URL_INVALID, f"{role} endpoint must be http(s)"),
            ),
        )
    return DialResolution(
        role=role,
        dial=Dial(role=role, model=effective_model, base_url=effective_url, api_key=api_key),
    )


# ── the per-call record (criterion 1) ────────────────────────────────────────

KIND_PREAMBLE = "preamble"
KIND_CALL = "call"
KIND_ATTEMPT = "attempt"
KIND_CELL = "cell"
KIND_ANALYSIS = "analysis"

#: league's word for a completion that ran out of budget mid-thought, reused so
#: one vocabulary spans every harness here. An INSTRUMENT event, never a
#: reasoning failure.
FINISH_TRUNCATED = ws.FINISH_TRUNCATED

#: The finish reason recorded for a call that never completed a round trip.
#: Distinct from an empty ``finish_reason``, which means the server sent one and
#: it was blank.
FINISH_TRANSPORT_FAILURE = "transport_failure"

#: Where a reasoning-token count came from. Recorded because the three cases
#: are genuinely different and a reader must be able to tell them apart.
#: (``nosec B105``: bandit reads any constant whose name contains TOKEN as a
#: credential. These are usage-payload paths, matching `arena_series.py`'s
#: ``VERDICT_PASS`` precedent for the same false positive.)
TOKEN_DETAIL_USAGE_DETAILS = "usage.completion_tokens_details.reasoning_tokens"  # nosec B105
TOKEN_DETAIL_USAGE_FLAT = "usage.reasoning_tokens"  # nosec B105
TOKEN_DETAIL_ABSENT = "absent"  # nosec B105
TOKEN_DETAIL_SCRIPTED = "scripted"  # nosec B105


def reasoning_tokens_from(usage: Mapping[str, Any]) -> tuple[Optional[int], str]:
    """Read a reasoning-token count, or report honestly that there is none.

    ``None`` rather than ``0``: a thinking model whose server reports no
    breakdown has not thought about nothing, and folding the unknown into the
    known is how a cost table stops being evidence.
    """
    details = usage.get("completion_tokens_details")
    if isinstance(details, Mapping) and isinstance(details.get("reasoning_tokens"), int):
        return int(details["reasoning_tokens"]), TOKEN_DETAIL_USAGE_DETAILS
    if isinstance(usage.get("reasoning_tokens"), int):
        return int(usage["reasoning_tokens"]), TOKEN_DETAIL_USAGE_FLAT
    return None, TOKEN_DETAIL_ABSENT


@dataclass(frozen=True)
class CallContext:
    """Which cell a call belongs to. Carried onto every record it produces."""

    arm: str
    rung: str
    problem: str
    route: str
    senses_hash: str
    live: bool


@dataclass(frozen=True)
class CallRecord:
    """One model call, fully accounted. The #37 workaround, made first-class."""

    arm: str
    rung: str
    problem: str
    route: str
    role: str
    model: str
    index: int
    finish_reason: str
    truncated: bool
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: Optional[int]
    content_tokens: Optional[int]
    token_detail: str
    reasoning_chars: int
    content_chars: int
    seconds: float
    messages_in: int
    tool_calls: tuple[str, ...]
    retries: int
    live: bool
    temperature: float
    thinking: str
    max_tokens: int
    senses_config_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": KIND_CALL,
            "arm": self.arm,
            "rung": self.rung,
            "problem": self.problem,
            "route": self.route,
            "role": self.role,
            "model": self.model,
            "index": self.index,
            "finish_reason": self.finish_reason,
            "truncated": self.truncated,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "content_tokens": self.content_tokens,
            "token_detail": self.token_detail,
            "reasoning_chars": self.reasoning_chars,
            "content_chars": self.content_chars,
            "seconds": round(self.seconds, 3),
            "messages_in": self.messages_in,
            "tool_calls": list(self.tool_calls),
            "retries": self.retries,
            "live": self.live,
            "temperature": self.temperature,
            "thinking": self.thinking,
            "max_tokens": self.max_tokens,
            "senses_config_hash": self.senses_config_hash,
        }


class CallLog:
    """Every call, in order. One record per model turn, retries included in it."""

    def __init__(self) -> None:
        self.records: list[CallRecord] = []
        self._sinks: list[Callable[[CallRecord], None]] = []

    def subscribe(self, sink: Callable[[CallRecord], None]) -> None:
        """Flush each record as it lands, not once at the end.

        A transient gateway failure 41 minutes into a prior series destroyed
        every turn already recorded, because the transcript was written only
        after the last run completed.
        """
        self._sinks.append(sink)

    def mint(
        self,
        ctx: CallContext,
        *,
        role: str,
        model: str,
        sampling: Sampling,
        finish_reason: str,
        prompt_tokens: int,
        completion_tokens: int,
        reasoning_tokens: Optional[int],
        token_detail: str,
        reasoning_chars: int,
        content_chars: int,
        seconds: float,
        messages_in: int,
        tool_calls: Sequence[str],
        retries: int,
    ) -> CallRecord:
        content_tokens = (
            None if reasoning_tokens is None else max(completion_tokens - reasoning_tokens, 0)
        )
        record = CallRecord(
            arm=ctx.arm,
            rung=ctx.rung,
            problem=ctx.problem,
            route=ctx.route,
            role=role,
            model=model,
            index=len(self.records),
            finish_reason=finish_reason,
            truncated=finish_reason == FINISH_TRUNCATED,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            reasoning_tokens=reasoning_tokens,
            content_tokens=content_tokens,
            token_detail=token_detail,
            reasoning_chars=reasoning_chars,
            content_chars=content_chars,
            seconds=seconds,
            messages_in=messages_in,
            tool_calls=tuple(tool_calls),
            retries=retries,
            live=ctx.live,
            temperature=sampling.temperature,
            thinking=sampling.thinking,
            max_tokens=sampling.max_tokens,
            senses_config_hash=ctx.senses_hash,
        )
        self.records.append(record)
        for sink in self._sinks:
            sink(record)
        return record

    @property
    def truncated(self) -> int:
        return sum(1 for record in self.records if record.truncated)

    def since(self, mark: int) -> list[CallRecord]:
        return self.records[mark:]


def fold_cost(records: Iterable[CallRecord]) -> dict[str, Any]:
    """What a set of calls cost. Reasoning is reported apart from content.

    ``reasoning_tokens`` folds only the calls whose server reported a
    breakdown, and ``reasoning_tokens_reported`` says how many that was — a sum
    over a partially-reported set is otherwise a number nobody can interpret.
    """
    records = list(records)
    reported = [record for record in records if record.reasoning_tokens is not None]
    return {
        "calls": len(records),
        "prompt_tokens": sum(record.prompt_tokens for record in records),
        "completion_tokens": sum(record.completion_tokens for record in records),
        "reasoning_tokens": sum(int(record.reasoning_tokens or 0) for record in reported),
        "content_tokens": sum(int(record.content_tokens or 0) for record in reported),
        "reasoning_tokens_reported": len(reported),
        "reasoning_chars": sum(record.reasoning_chars for record in records),
        "content_chars": sum(record.content_chars for record in records),
        "seconds": round(sum(record.seconds for record in records), 3),
        "truncated_calls": sum(1 for record in records if record.truncated),
        "retries": sum(record.retries for record in records),
        "finish_reasons": _count(record.finish_reason for record in records),
    }


def _count(values: Iterable[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        out[value] = out.get(value, 0) + 1
    return out


# ── the seams ────────────────────────────────────────────────────────────────


class ArchSeam(ws.WorkerSeam):
    """t2's metered seam, plus this series' per-call record.

    Subclassed rather than copied: the retry ladder, the truncation notice, the
    audited ``urlopen`` and the key handling are t2's and stay t2's. Two things
    are added, both at :meth:`_post`:

    * the thinking-mode wire keys from the committed configuration are merged
      into the body — so what is *recorded* as the thinking mode and what is
      *sent* cannot drift apart;
    * the raw payload is kept, because that is the only place a
      ``finish_reason`` and a reasoning-token breakdown exist.

    :meth:`_post` shapes the body and :meth:`_transport` performs the round
    trip. Tests patch ``_transport``, which is what keeps the body-shaping step
    inside the code under test rather than around it.
    """

    def __init__(
        self,
        *,
        dial: Dial,
        sampling: Sampling,
        wire_extra: Mapping[str, Any],
        role: str,
        ctx: CallContext,
        log: CallLog,
        tools: Optional[list[dict[str, Any]]] = None,
        sleep: Optional[Callable[[float], None]] = None,
    ) -> None:
        kwargs: dict[str, Any] = {}
        if sleep is not None:
            kwargs["sleep"] = sleep
        super().__init__(
            base_url=dial.base_url,
            model=dial.model,
            api_key=dial.api_key,
            role=role,
            max_tokens=sampling.max_tokens,
            temperature=sampling.temperature,
            tools=tools,
            **kwargs,
        )
        self.sampling = sampling
        self.wire_extra = dict(wire_extra)
        self.ctx = ctx
        self.log = log
        self._last_payload: dict[str, Any] = {}

    def _transport(self, body: dict[str, Any]) -> dict[str, Any]:
        """The round trip itself — t2's, unchanged."""
        return super()._post(body)

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        merged = dict(body)
        merged.update(self.wire_extra)
        payload = self._transport(merged)
        self._last_payload = payload if isinstance(payload, dict) else {}
        return payload

    def __call__(self, messages: list[dict[str, Any]]) -> ModelResponse:
        seconds_before = self.meter.seconds
        retries_before = self.meter.retries
        self._last_payload = {}
        try:
            reply = super().__call__(messages)
        except ws.WorkerTransportError:
            # A failed call is a call. It lands a record before the raise, so a
            # cell's call count and its transcript never disagree.
            self.log.mint(
                self.ctx,
                role=self.meter.role,
                model=self.model,
                sampling=self.sampling,
                finish_reason=FINISH_TRANSPORT_FAILURE,
                prompt_tokens=0,
                completion_tokens=0,
                reasoning_tokens=None,
                token_detail=TOKEN_DETAIL_ABSENT,
                reasoning_chars=0,
                content_chars=0,
                seconds=self.meter.seconds - seconds_before,
                messages_in=len(messages),
                tool_calls=(),
                retries=self.meter.retries - retries_before,
            )
            raise

        payload = self._last_payload
        choice = ((payload.get("choices") or [{}])[0]) or {}
        usage = payload.get("usage") or {}
        reasoning_tokens, token_detail = reasoning_tokens_from(usage)
        self.log.mint(
            self.ctx,
            role=self.meter.role,
            model=self.model,
            sampling=self.sampling,
            finish_reason=str(choice.get("finish_reason") or ""),
            prompt_tokens=reply.prompt_tokens,
            completion_tokens=reply.completion_tokens,
            reasoning_tokens=reasoning_tokens,
            token_detail=token_detail,
            reasoning_chars=len(reply.reasoning or ""),
            content_chars=len(reply.content or ""),
            seconds=self.meter.seconds - seconds_before,
            messages_in=len(messages),
            tool_calls=tuple(call.name for call in reply.tool_calls),
            retries=self.meter.retries - retries_before,
        )
        return reply


class ScriptedArchSeam:
    """The hermetic stand-in, recorded the same way. Never touches a network.

    ``seconds`` is ``0.0`` rather than a measured interval: a scripted mind's
    wall clock measures this process, not a model, and a plausible-looking
    number in a cost column is worse than an obvious zero.
    """

    def __init__(
        self,
        complete: Callable[[list[dict[str, Any]]], ModelResponse],
        *,
        role: str,
        model: str,
        sampling: Sampling,
        ctx: CallContext,
        log: CallLog,
        finish_reasons: Any = "stop",
        tools: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        self._complete = complete
        self.role = role
        self.model = model
        self.sampling = sampling
        self.ctx = ctx
        self.log = log
        self.tools = tools
        self._finish_reasons = (
            [finish_reasons] if isinstance(finish_reasons, str) else list(finish_reasons)
        )
        self._turn = 0

    def _finish_reason(self) -> str:
        if not self._finish_reasons:
            return "stop"
        index = min(self._turn, len(self._finish_reasons) - 1)
        return str(self._finish_reasons[index])

    def __call__(self, messages: list[dict[str, Any]]) -> ModelResponse:
        reply = self._complete(messages)
        reason = self._finish_reason()
        self._turn += 1
        self.log.mint(
            self.ctx,
            role=self.role,
            model=self.model,
            sampling=self.sampling,
            finish_reason=reason,
            prompt_tokens=reply.prompt_tokens,
            completion_tokens=reply.completion_tokens,
            reasoning_tokens=None,
            token_detail=TOKEN_DETAIL_SCRIPTED,
            reasoning_chars=len(reply.reasoning or ""),
            content_chars=len(reply.content or ""),
            seconds=0.0,
            messages_in=len(messages),
            tool_calls=tuple(call.name for call in reply.tool_calls),
            retries=0,
        )
        return reply


class ScriptedSeams:
    """Builds one scripted, recorded mind per role. The hermetic lane."""

    live = False

    def __init__(
        self,
        minds: Optional[Mapping[str, Callable[[list[dict[str, Any]]], ModelResponse]]] = None,
        *,
        config: ArchConfig,
        log: CallLog,
        finish_reasons: Any = "stop",
    ) -> None:
        self.minds = dict(minds or {})
        self.config = config
        self.log = log
        self.finish_reasons = finish_reasons

    def model_for(self, role: str) -> str:
        return f"scripted:{role}"

    def build(
        self, role: str, ctx: CallContext, tools: Optional[list[dict[str, Any]]]
    ) -> ScriptedArchSeam:
        mind = self.minds.get(role) or default_scripted_mind(role, tools)
        return ScriptedArchSeam(
            mind,
            role=role,
            model=self.model_for(role),
            sampling=self.config.sampling_for(ctx.arm, role),
            ctx=ctx,
            log=self.log,
            finish_reasons=self.finish_reasons,
            tools=tools,
        )


class LiveSeams:
    """Builds one dialled, recorded mind per role. The only path to a network."""

    live = True

    def __init__(
        self,
        *,
        config: ArchConfig,
        log: CallLog,
        dials: Mapping[str, Dial],
        sleep: Optional[Callable[[float], None]] = None,
    ) -> None:
        self.config = config
        self.log = log
        self.dials = dict(dials)
        self.sleep = sleep

    def model_for(self, role: str) -> str:
        dial = self.dials.get(role)
        return dial.model if dial else ""

    def build(self, role: str, ctx: CallContext, tools: Optional[list[dict[str, Any]]]) -> ArchSeam:
        if role not in self.dials:
            raise ConfigError(f"no resolved dial for role {role!r}; the arm is ABSENT")
        sampling = self.config.sampling_for(ctx.arm, role)
        return ArchSeam(
            dial=self.dials[role],
            sampling=sampling,
            wire_extra=self.config.wire_extra(sampling.thinking),
            role=role,
            ctx=ctx,
            log=self.log,
            tools=tools,
            sleep=self.sleep,
        )


# ── the problems, cited from the committed record ────────────────────────────

DIFFICULTY_SIMPLE = "simple"
DIFFICULTY_COMPLEX = "complex"
DIFFICULTIES = (DIFFICULTY_SIMPLE, DIFFICULTY_COMPLEX)

#: ``docs/challenge-problems.md``'s own vocabulary for what each problem breaks.
FAILURE_PROTOCOL = "protocol"
FAILURE_CAPACITY = "capacity"


@dataclass(frozen=True)
class Problem:
    """One graded problem, adapted from the harness that owns it.

    Nothing here restates a problem statement, an answer or a grader: each is
    imported from the ``examples/challenge_*.py`` module that owns it, whose
    statements and verified answers live in ``docs/challenge-problems.md``. A
    problem re-typed into a second file is a problem with two answers.
    """

    id: str
    statement: str
    difficulty: str
    failure_class: str
    source: str
    tools: tuple[dict[str, Any], ...]
    make_bench: Callable[[], Any]
    grade: Callable[[str], dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "difficulty": self.difficulty,
            "failure_class": self.failure_class,
            "source": self.source,
            "tools": [entry["function"]["name"] for entry in self.tools],
        }


def _grade_subset(raw: str) -> dict[str, Any]:
    """Adapt the subset grader, which takes an int, to a raw finish summary."""
    text = (raw or "").strip()
    try:
        answer = int(text)
    except (TypeError, ValueError):
        return {
            "answer": None,
            "expected": challenge_subset.truth(),
            "is_correct": False,
            "verdict": "NO ANSWER",
        }
    return challenge_subset.grade(answer)


PROBLEMS: dict[str, Problem] = {
    "subset": Problem(
        id="subset",
        statement=challenge_subset.PROBLEM,
        # Tractable in the head -- docs/challenge-problems.md classes its failure
        # as PROTOCOL ("the mind can do the work but reasons past the tool
        # call"), which is what makes it the routable-simple end of a rung.
        difficulty=DIFFICULTY_SIMPLE,
        failure_class=FAILURE_PROTOCOL,
        source="docs/challenge-problems.md §1; examples/challenge_subset.py",
        tools=tuple(challenge_subset.TOOLS),
        make_bench=challenge_subset.SubsetBench,
        grade=_grade_subset,
    ),
    "register": Problem(
        id="register",
        statement=challenge_register.PROBLEM,
        # 1,920 states. The same doc classes it a CAPACITY failure: "the plan is
        # right and there is no way to run it".
        difficulty=DIFFICULTY_COMPLEX,
        failure_class=FAILURE_CAPACITY,
        source="docs/challenge-problems.md §2; examples/challenge_register.py",
        tools=tuple(challenge_register.TOOLS),
        make_bench=challenge_register.RegisterBench,
        grade=challenge_register.grade,
    ),
    "entropic": Problem(
        id="entropic",
        statement=challenge_entropic.PROBLEM_3,
        difficulty=DIFFICULTY_COMPLEX,
        failure_class=FAILURE_CAPACITY,
        source="docs/challenge-problems.md §3; examples/challenge_entropic.py",
        tools=tuple(challenge_entropic.TOOLS),
        make_bench=challenge_entropic.EntropicBench,
        grade=challenge_entropic.grade,
    ),
    "entropic_constrained": Problem(
        id="entropic_constrained",
        statement=challenge_entropic.PROBLEM_3B,
        difficulty=DIFFICULTY_COMPLEX,
        failure_class=FAILURE_CAPACITY,
        source="docs/challenge-problems.md §3b; examples/challenge_entropic.py",
        tools=tuple(challenge_entropic.TOOLS),
        make_bench=challenge_entropic.EntropicBench,
        grade=challenge_entropic.grade_constrained,
    ),
}


@dataclass(frozen=True)
class Rung:
    """One rung: the problems an arm must answer, in one cell.

    Heterogeneity is *computed* from the problems rather than declared, because
    a flag a human sets is a flag a human sets wrongly — and this one decides
    whether the hybrid arm's cell counts at all.
    """

    id: str
    problems: tuple[str, ...]
    why: str

    def difficulties(self, registry: Mapping[str, Problem]) -> frozenset[str]:
        return frozenset(registry[name].difficulty for name in self.problems if name in registry)

    def heterogeneous(self, registry: Mapping[str, Problem]) -> bool:
        """Can the hybrid arm's routing decision actually vary on this rung?

        On a single-difficulty rung the hybrid is structurally identical to the
        manager or to a flat arm, and its cell measures nothing. Such a cell is
        excluded from the verdict **by rule**, before the data arrives — not by
        judgement afterwards.
        """
        return len(self.difficulties(registry)) > 1

    def to_dict(self, registry: Mapping[str, Problem]) -> dict[str, Any]:
        return {
            "id": self.id,
            "problems": list(self.problems),
            "difficulties": sorted(self.difficulties(registry)),
            "heterogeneous": self.heterogeneous(registry),
            "why": self.why,
        }


LADDER: tuple[Rung, ...] = (
    Rung(
        id="C1",
        problems=("subset",),
        why=(
            "the protocol rung: 144 candidate subsets with a planted trap at 72. "
            "One difficulty, so the hybrid cell is degenerate here by rule"
        ),
    ),
    Rung(
        id="C2",
        problems=("register",),
        why=(
            "the first capacity rung: 1,920 states, an exhaustive search no mind "
            "does reliably in its head. One difficulty; hybrid degenerate"
        ),
    ),
    Rung(
        id="C3",
        problems=("entropic", "entropic_constrained"),
        why=(
            "30,720 states, and the matched pair: 3 is under-determined at 17 "
            "solutions and 3b is unique, so a confabulating mind cannot pass both. "
            "Both complex; hybrid degenerate"
        ),
    ),
    Rung(
        id="C4",
        problems=("subset", "register", "entropic"),
        why=(
            "the mixed battery, and the only rung on this ladder where the "
            "hybrid's routing has room to vary: one tractable protocol problem "
            "beside two capacity searches"
        ),
    ),
)

LADDER_BY_ID = {rung.id: rung for rung in LADDER}


# ── tool surfaces (h28: the harness passes exactly this) ─────────────────────

#: t1's enumeration, reused rather than restated. ``finish`` lives here and on
#: no other surface, which is what "the cortex keeps final authority" means.
ORCHESTRATION_TOOLS = ot.ORCHESTRATOR_TOOLS
FINAL_AUTHORITY_TOOLS = ot.FINAL_AUTHORITY_TOOLS
DELEGATION_MARKER = ot.DELEGATION_MARKER

#: The worker's terminal verb. It ends the child's own drive and hands text
#: back; it can never end the task.
WORKER_TERMINAL_TOOL = "report"

_DELEGATE_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "delegate",
        "description": (
            "Hand one scoped subtask to the worker. It reports back to you and "
            "cannot act further. Say why you are routing this one out."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "subtask": {"type": "string", "description": "What the worker should do."},
                "reason": {
                    "type": "string",
                    "description": "Why this work goes to the worker rather than staying with you.",
                },
            },
            "required": ["subtask", "reason"],
        },
    },
}

_NOTE_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "note",
        "description": "Record a working note for yourself.",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
}

_FINISH_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "finish",
        "description": "Submit the final answer. Yours to write, never the worker's.",
        "parameters": {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        },
    },
}

_REPORT_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": WORKER_TERMINAL_TOOL,
        "description": "Hand your findings back to the orchestrator. Your only terminal verb.",
        "parameters": {
            "type": "object",
            "properties": {"findings": {"type": "string"}},
            "required": ["findings"],
        },
    },
}


def _bench_schema(problem: Problem) -> list[dict[str, Any]]:
    """The problem's own tools, minus its ``finish``.

    Dropping ``finish`` is not tidying: it is the wire-level half of "no second
    final authority". The orchestrated surfaces supply their own ``finish``,
    and the worker's surface supplies none.
    """
    return [
        dict(entry)
        for entry in problem.tools
        if entry["function"]["name"] not in FINAL_AUTHORITY_TOOLS
    ]


def worker_tools_for(problem: Problem) -> tuple[str, ...]:
    """The worker's complete verb list on this problem. Quotable as-is."""
    return tuple(entry["function"]["name"] for entry in _bench_schema(problem)) + (
        WORKER_TERMINAL_TOOL,
    )


def worker_schema_for(problem: Problem) -> list[dict[str, Any]]:
    """The schema the worker actually receives: the bench, and ``report``."""
    return [*_bench_schema(problem), dict(_REPORT_SCHEMA)]


def orchestration_schema(problem: Problem, *, keeps_work: bool) -> list[dict[str, Any]]:
    """The orchestrator's schema. ``keeps_work`` is the manager/hybrid split.

    The manager holds ``delegate``/``note``/``finish`` and no ground-work verb
    at all — the worker executes everything. The hybrid holds the bench too, so
    routing is a decision it can actually make rather than a label on a drive.
    """
    base = [dict(_DELEGATE_SCHEMA), dict(_NOTE_SCHEMA), dict(_FINISH_SCHEMA)]
    if keeps_work:
        return [*_bench_schema(problem), *base]
    return base


# ── the prompts ──────────────────────────────────────────────────────────────

FLAT_SYSTEM = (
    "You are working this problem alone with the tools you have been given. Use "
    "them to check your reasoning rather than trusting it, and when you have an "
    "answer, call `finish` with it."
)

ORCHESTRATOR_SYSTEM = (
    "You are working this problem with a worker available to you. Hand a scoped "
    "subtask to the worker with `delegate`, saying why you are routing it out; "
    "the worker reports back and cannot act further. Weigh what it returns, then "
    "answer with `finish` — the final answer is yours to write, never the worker's."
)

WORKER_SYSTEM = (
    "You have been given one scoped subtask. Work it out with the tools you have "
    "and hand your findings back with `report`. You do not decide what happens "
    "next and you do not write the final answer; reporting back is the whole of "
    "your job."
)


def top_level_prompt(base: str, *, identity: Optional[str] = None) -> str:
    """Frame the top-level acting loop — whichever role is driving it.

    In arms E, M and H that is the cortex; in W it is the worker, which *is*
    the top-level mind and is framed as such. The framing renames who is
    speaking and never what they may do (colleague#352), and with no configured
    identity it returns *base* byte for byte — the acceptance criterion that
    keeps the feature honest.
    """
    return ot.orchestrator_prompt(base, identity=identity)


# ── the executors ────────────────────────────────────────────────────────────


class BenchWorkerExecutor:
    """The worker's whole surface: the problem's bench, and ``report``.

    Shaped exactly like t1's ``WorkerExecutor`` — ``report`` ends the *child's*
    drive and hands its findings back — with the problem's own tools folded in,
    because "the worker executes the ground work" means it needs the ground
    work's tools. A verb outside :func:`worker_tools_for` raises
    :class:`~embodiment.loop.UnknownToolError` rather than returning a polite
    error string: a narrowed surface has to bite, and a ``ToolOutcome`` carrying
    "unknown tool" is a *success* the model reads as prose.
    """

    def __init__(self, *, problem: Problem, subtask: str) -> None:
        self.problem = problem
        self.subtask = subtask
        self.bench = problem.make_bench()
        self.surface = worker_tools_for(problem)
        self.calls: list[str] = []
        self.refused: list[str] = []
        self.report: str = ""

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.calls.append(name)
        if name not in self.surface:
            self.refused.append(name)
            raise UnknownToolError(
                f"{name!r} is not available to a worker; its tools are {list(self.surface)}"
            )
        if name == WORKER_TERMINAL_TOOL:
            findings = str(arguments.get("findings") or "").strip()
            if not findings:
                raise ToolError("report requires `findings`: say what you worked out")
            self.report = findings
            return ToolOutcome(
                result=f"report recorded: {findings}",
                finished=True,
                finish_summary=findings,
            )
        return self.bench.execute(name, arguments)

    def state(self) -> str:
        return f"{len(self.calls)} tool call(s) on {self.problem.id}"


@dataclass
class RoutingDecision:
    """One routing decision the orchestrator made, with the reason it gave."""

    problem: str
    routed_to: str
    reason: str
    subtask: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "problem": self.problem,
            "routed_to": self.routed_to,
            "reason": self.reason,
            "subtask": self.subtask,
        }


class ArchOrchestrator:
    """The orchestrator's surface for one problem, in one arm.

    The *mechanism* underneath is t1's and untouched: ``delegate`` returns a
    :class:`~embodiment.subagent.SpawnRequest`, and the loop's own spawn
    machinery attenuates the child, charges its turns to this budget and
    attributes its degradations. What lives here is the tool *surface*, which is
    the host's job — the manager holds no ground-work verb, the hybrid holds the
    problem's bench, and only this surface holds ``finish``.
    """

    def __init__(
        self,
        *,
        problem: Problem,
        arm: Arm,
        task_id: str,
        worker_model: str = "",
        worker_max_steps: int,
        identity: Optional[str] = None,
    ) -> None:
        self.problem = problem
        self.arm = arm
        self.task_id = task_id
        self.worker_model = worker_model
        self.worker_max_steps = worker_max_steps
        self.identity = identity
        self.bench = problem.make_bench() if arm.keeps_work else None
        self.surface = tuple(
            entry["function"]["name"]
            for entry in orchestration_schema(problem, keeps_work=arm.keeps_work)
        )
        self.calls: list[str] = []
        self.refused: list[str] = []
        self.notes: list[str] = []
        self.children: list[BenchWorkerExecutor] = []
        self.routing: list[RoutingDecision] = []
        self.delegations = 0
        self.finished = False
        self.finish_summary = ""

    def child_task_id(self, ordinal: int) -> str:
        return f"{self.task_id}-worker-{ordinal}"

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.calls.append(name)
        if name not in self.surface:
            self.refused.append(name)
            raise UnknownToolError(
                f"{name!r} is not available to the orchestrator in arm "
                f"{self.arm.id}; its tools are {list(self.surface)}"
            )
        if name == "delegate":
            return self._delegate(arguments)
        if name == "note":
            text = str(arguments.get("text") or "").strip()
            if not text:
                raise ToolError("note requires `text`")
            self.notes.append(text)
            return ToolOutcome(result="noted")
        if name == "finish":
            answer = str(arguments.get("answer", "")).strip()
            self.finished = True
            self.finish_summary = answer
            return ToolOutcome(result="submitted", finished=True, finish_summary=answer)
        # Everything left is a ground-work verb, which only the hybrid holds.
        # Surface membership already guarantees a bench; the explicit refusal is
        # here so a future surface edit fails loudly rather than on ``None``.
        if self.bench is None:
            self.refused.append(name)
            raise UnknownToolError(
                f"{name!r} is ground work and arm {self.arm.id} delegates all of it"
            )
        return self.bench.execute(name, arguments)

    def _delegate(self, arguments: dict[str, Any]) -> ToolOutcome:
        subtask = str(arguments.get("subtask") or "").strip()
        if not subtask:
            raise ToolError("delegate requires a `subtask`: say what the worker should do")
        reason = str(arguments.get("reason") or "").strip()
        self.delegations += 1
        self.routing.append(
            RoutingDecision(
                problem=self.problem.id,
                routed_to=ROLE_WORKER,
                reason=reason,
                subtask=subtask,
            )
        )
        child_executor = BenchWorkerExecutor(problem=self.problem, subtask=subtask)
        self.children.append(child_executor)
        child_task = Task(
            id=self.child_task_id(self.delegations),
            repo_path=ot.NO_REPO,
            instruction=self._worker_instruction(subtask),
        )
        return ToolOutcome(
            result=f"{DELEGATION_MARKER} {subtask}",
            spawn=ot.SpawnRequest(
                task=child_task,
                executor=child_executor,
                role=ot.ROLE_WORKER,
                # A LEAF: the worker may consult no one. Asking for less than the
                # loop would grant is the one direction a request may move a bound.
                allowance=ot.NO_SPAWNS,
                max_steps=self.worker_max_steps,
                system_prompt=ot.frame_subagent(WORKER_SYSTEM, identity=self.identity),
                model=self.worker_model,
                context={"subtask": subtask, "problem": self.problem.id},
            ),
        )

    def _worker_instruction(self, subtask: str) -> str:
        """The child sees the problem AND the boundary drawn around its part."""
        return (
            f"{self.problem.statement}\n\n"
            f"Subtask: {subtask}\n\n"
            "Work only this subtask. When you have an answer, call `report` with "
            "your findings. You cannot submit the final answer."
        )


# ── running one attempt ──────────────────────────────────────────────────────


@dataclass
class AttemptRecord:
    """One arm's attempt at one problem, and everything it cost."""

    arm: str
    rung: str
    problem: str
    route: str
    top_level_role: str
    exit_reason: str = ""
    model_turns: int = 0
    child_model_turns: int = 0
    delegations: int = 0
    routed_to: str = ""
    routing: list[dict[str, Any]] = field(default_factory=list)
    raw_summary: str = ""
    graded: dict[str, Any] = field(default_factory=dict)
    is_correct: bool = False
    degradation_codes: list[str] = field(default_factory=list)
    refused_tools: list[str] = field(default_factory=list)
    calls: int = 0
    cost: dict[str, Any] = field(default_factory=dict)
    aborted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": KIND_ATTEMPT,
            "arm": self.arm,
            "rung": self.rung,
            "problem": self.problem,
            "route": self.route,
            "top_level_role": self.top_level_role,
            "exit_reason": self.exit_reason,
            "model_turns": self.model_turns,
            "child_model_turns": self.child_model_turns,
            "delegations": self.delegations,
            "routed_to": self.routed_to,
            "routing": list(self.routing),
            "raw_summary": self.raw_summary,
            "graded": dict(self.graded),
            "is_correct": self.is_correct,
            "degradation_codes": list(self.degradation_codes),
            "refused_tools": list(self.refused_tools),
            "calls": self.calls,
            "cost": dict(self.cost),
            "aborted": self.aborted,
        }


def run_attempt(
    *,
    arm: Arm,
    rung: str,
    problem: Problem,
    seams: Any,
    config: ArchConfig,
    senses_hash: str,
    route: str = ROUTE_TEXT,
    identity: Optional[str] = None,
) -> AttemptRecord:
    """Drive one arm at one problem, and grade what comes back.

    The two flat arms differ only in which role drives the loop. The two
    orchestrated arms differ only in ``keeps_work``. Nothing here branches on an
    arm *id*.
    """
    log: CallLog = seams.log
    mark = len(log.records)
    ctx = CallContext(
        arm=arm.id,
        rung=rung,
        problem=problem.id,
        route=route,
        senses_hash=senses_hash,
        live=bool(getattr(seams, "live", False)),
    )
    budget = config.budget_for(arm.id)
    task = Task(
        id=f"{arm.id}-{rung}-{problem.id}",
        repo_path=ot.NO_REPO,
        instruction=problem.statement,
    )
    record = AttemptRecord(
        arm=arm.id,
        rung=rung,
        problem=problem.id,
        route=route,
        top_level_role=arm.top_level_role,
        routed_to=arm.top_level_role,
    )

    if arm.shape == SHAPE_FLAT:
        executor: Any = problem.make_bench()
        mind = seams.build(arm.top_level_role, ctx, list(problem.tools))
        outcome, aborted = _drive(
            mind,
            task,
            executor=executor,
            max_steps=budget.max_steps,
            system_prompt=top_level_prompt(FLAT_SYSTEM, identity=identity),
            model=seams.model_for(arm.top_level_role),
        )
    else:
        executor = ArchOrchestrator(
            problem=problem,
            arm=arm,
            task_id=task.id,
            worker_model=seams.model_for(ROLE_WORKER),
            worker_max_steps=budget.worker_max_steps,
            identity=identity,
        )
        cortex = seams.build(
            ROLE_CORTEX, ctx, orchestration_schema(problem, keeps_work=arm.keeps_work)
        )
        worker = seams.build(ROLE_WORKER, ctx, worker_schema_for(problem))
        outcome, aborted = _drive(
            cortex,
            task,
            executor=executor,
            max_steps=budget.max_steps,
            system_prompt=top_level_prompt(ORCHESTRATOR_SYSTEM, identity=identity),
            model=seams.model_for(ROLE_CORTEX),
            subagent=ot.build_worker_seam(worker),
            spawn_allowance=budget.spawn_allowance,
        )
        record.delegations = executor.delegations
        record.routing = [decision.to_dict() for decision in executor.routing]
        if executor.delegations:
            record.routed_to = ROLE_WORKER

    record.aborted = aborted
    record.exit_reason = outcome.exit_reason
    record.model_turns = outcome.result.stats.model_turns
    record.child_model_turns = outcome.child_model_turns
    record.refused_tools = list(getattr(executor, "refused", []))
    record.degradation_codes = [entry.code for entry in ledger.read(loop=outcome)]

    record.raw_summary = (outcome.result.summary or "").strip()
    record.graded = problem.grade(record.raw_summary)
    record.is_correct = bool(record.graded.get("is_correct"))

    calls = log.since(mark)
    record.calls = len(calls)
    record.cost = fold_cost(calls)
    return record


def _drive(mind: Any, task: Task, **kwargs: Any) -> tuple[Any, bool]:
    """Run the loop, and treat an abort as data rather than an exception.

    A drive that falls over is a measured outcome of that architecture; killing
    the series over it would discard every cell already paid for.
    """
    try:
        return run(mind, task, **kwargs), False
    except LoopAborted as aborted:
        return aborted.outcome, True


# ── running a cell, a rung, a series ─────────────────────────────────────────


@dataclass
class CellResult:
    """One arm at one rung, on one route. The unit :func:`analyse` grades."""

    arm: str
    rung: str
    route: str
    senses_config_hash: str
    attempts: list[AttemptRecord] = field(default_factory=list)

    @property
    def attempted(self) -> int:
        return len(self.attempts)

    @property
    def correct(self) -> int:
        return sum(1 for attempt in self.attempts if attempt.is_correct)

    def to_dict(self) -> dict[str, Any]:
        cost = _sum_costs(attempt.cost for attempt in self.attempts)
        return {
            "kind": KIND_CELL,
            "arm": self.arm,
            "rung": self.rung,
            "route": self.route,
            "senses_config_hash": self.senses_config_hash,
            "attempted": self.attempted,
            "correct": self.correct,
            "problems": [attempt.problem for attempt in self.attempts],
            "delegations": sum(attempt.delegations for attempt in self.attempts),
            "model_turns": sum(attempt.model_turns for attempt in self.attempts),
            "child_model_turns": sum(attempt.child_model_turns for attempt in self.attempts),
            "aborted": sum(1 for attempt in self.attempts if attempt.aborted),
            "calls": int(cost.get("calls", 0)),
            "truncated_calls": int(cost.get("truncated_calls", 0)),
            "cost": cost,
        }


_COST_SUMS = (
    "calls",
    "prompt_tokens",
    "completion_tokens",
    "reasoning_tokens",
    "content_tokens",
    "reasoning_tokens_reported",
    "reasoning_chars",
    "content_chars",
    "truncated_calls",
    "retries",
)


def _sum_costs(costs: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    costs = list(costs)
    folded: dict[str, Any] = {
        key: sum(int(cost.get(key, 0)) for cost in costs) for key in _COST_SUMS
    }
    folded["seconds"] = round(sum(float(cost.get("seconds", 0.0)) for cost in costs), 3)
    reasons: dict[str, int] = {}
    for cost in costs:
        for reason, count in (cost.get("finish_reasons") or {}).items():
            reasons[reason] = reasons.get(reason, 0) + int(count)
    folded["finish_reasons"] = reasons
    return folded


def _append_jsonl(path: Optional[Path], record: Mapping[str, Any]) -> None:
    if path is None:
        return
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def preamble(
    config: ArchConfig,
    *,
    senses_hash: str,
    live: bool,
    ladder: Sequence[Rung],
    registry: Mapping[str, Problem],
) -> dict[str, Any]:
    """The configuration that produced a run, written before its first result."""
    payload = config.to_dict()
    payload.update(
        {
            "kind": KIND_PREAMBLE,
            "series": "orchestrator-worker-architectures",
            "task": "t5",
            "config_path": str(config.path) if config.path else None,
            "senses_config_hash": senses_hash,
            "live": live,
            "arms": {arm: ARMS[arm].to_dict() for arm in ARM_ORDER},
            "flat_arms": list(FLAT_ARMS),
            "routes": {route: ROUTE_WHY.get(route, "") for route in ROUTES},
            "ladder": [rung.to_dict(registry) for rung in ladder],
            "problems": {name: problem.to_dict() for name, problem in registry.items()},
        }
    )
    return payload


def run_series(
    *,
    config: ArchConfig,
    seams: Any,
    log: CallLog,
    ladder: Sequence[Rung] = LADDER,
    registry: Mapping[str, Problem] = PROBLEMS,
    arms: Sequence[str] = ARM_ORDER,
    out: Optional[Path] = None,
    route: str = ROUTE_TEXT,
    identity: Optional[str] = None,
) -> dict[str, Any]:
    """Every arm at every rung, recorded as it goes.

    Refuses before the first call if the arms disagree about senses — a run
    whose control variable drifted is not worth the tokens.
    """
    senses_hash = assert_senses_identical(config)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.unlink(missing_ok=True)
        log.subscribe(lambda record: _append_jsonl(out, record.to_dict()))
    _append_jsonl(
        out,
        preamble(
            config,
            senses_hash=senses_hash,
            live=bool(getattr(seams, "live", False)),
            ladder=ladder,
            registry=registry,
        ),
    )

    cells: list[dict[str, Any]] = []
    for rung in ladder:
        for arm_id in arms:
            cell = CellResult(arm=arm_id, rung=rung.id, route=route, senses_config_hash=senses_hash)
            for problem_id in rung.problems:
                attempt = run_attempt(
                    arm=ARMS[arm_id],
                    rung=rung.id,
                    problem=registry[problem_id],
                    seams=seams,
                    config=config,
                    senses_hash=senses_hash,
                    route=route,
                    identity=identity,
                )
                cell.attempts.append(attempt)
                _append_jsonl(out, attempt.to_dict())
            payload = cell.to_dict()
            cells.append(payload)
            _append_jsonl(out, payload)

    return {
        "kind": "series",
        "live": bool(getattr(seams, "live", False)),
        "senses_config_hash": senses_hash,
        "rungs": [rung.id for rung in ladder],
        "arms": list(arms),
        "cells": cells,
        "cost": _sum_costs(cell["cost"] for cell in cells),
    }


# ── analysis: the rule, re-applied to a committed artifact ───────────────────

VERDICT_SEPARATED = "SEPARATED"
VERDICT_INCONCLUSIVE = "INCONCLUSIVE"
VERDICT_ABSENT = "ABSENT"
VERDICTS = (VERDICT_SEPARATED, VERDICT_INCONCLUSIVE, VERDICT_ABSENT)

STATE_UNRUN = "unrun"
STATE_REFUSED = "refused"
STATE_GRADED = "graded"

#: Why a present cell was left out of the verdict. Both are rules applied
#: before the data arrives, never judgements made after seeing it.
EXCLUSION_TRUNCATED = "truncated-calls"
EXCLUSION_DEGENERATE = "degenerate-rung"


def read_log(path: Path) -> list[dict[str, Any]]:
    """Every record in an artifact, in order. Unreadable lines are skipped."""
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            records.append(parsed)
    return records


def analyse(
    source: Any,
    *,
    config: Optional[ArchConfig] = None,
    registry: Mapping[str, Problem] = PROBLEMS,
    ladder: Sequence[Rung] = LADDER,
) -> dict[str, Any]:
    """Re-apply the decision rule to a committed artifact.

    Separate from the runner on purpose: a verdict only the process that
    produced the data can produce is not checkable.

    **The refusal is the load-bearing part.** A rung that has data but is
    missing a *gradeable* cell for either flat arm produces no verdict at all —
    not a hedged one, and not one computed from the arms that did run. Arm E
    without arm W cannot tell orchestration from the worker being sufficient;
    arm W without arm E cannot tell either from the rig it replaces. A rung with
    no cells at all is simply ``unrun``, which is what the stop rule leaves
    behind and is not the same failure.
    """
    resolved = config if config is not None else load_config()
    records = read_log(Path(source)) if isinstance(source, (str, Path)) else list(source)
    cells = [record for record in records if record.get("kind") == KIND_CELL]

    decision = resolved.decision
    margin_needed = int(decision.get("separation_margin", 1))
    drop_truncated = bool(decision.get("exclude_truncated_cells", True))
    hybrid_needs_mix = bool(decision.get("grade_hybrid_only_on_heterogeneous_rungs", True))

    by_key: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    for cell in cells:
        key = (str(cell.get("rung") or ""), str(cell.get("route") or ROUTE_TEXT))
        by_key.setdefault(key, {})[str(cell.get("arm") or "")] = cell

    keys: list[tuple[str, str]] = []
    for rung in ladder:
        routes = sorted({route for (rung_id, route) in by_key if rung_id == rung.id})
        for route in routes or [ROUTE_TEXT]:
            keys.append((rung.id, route))
    for key in sorted(by_key):
        if key not in keys:
            keys.append(key)

    ladder_by_id = {rung.id: rung for rung in ladder}
    rows: list[dict[str, Any]] = []
    for rung_id, route in keys:
        rung = ladder_by_id.get(rung_id)
        heterogeneous = rung.heterogeneous(registry) if rung else True
        rows.append(
            _grade_row(
                rung_id=rung_id,
                route=route,
                heterogeneous=heterogeneous,
                present=by_key.get((rung_id, route), {}),
                margin_needed=margin_needed,
                drop_truncated=drop_truncated,
                hybrid_needs_mix=hybrid_needs_mix,
            )
        )

    separated = next((row for row in rows if row["verdict"] == VERDICT_SEPARATED), None)
    graded = [row for row in rows if row["state"] == STATE_GRADED]
    if separated is not None:
        verdict = VERDICT_SEPARATED
    elif graded:
        verdict = VERDICT_INCONCLUSIVE
    else:
        verdict = VERDICT_ABSENT

    refused = [row["rung"] for row in rows if row["state"] == STATE_REFUSED]
    return {
        "kind": KIND_ANALYSIS,
        "source": str(source) if isinstance(source, (str, Path)) else "records",
        "config_path": str(resolved.path) if resolved.path else None,
        "decision": dict(decision),
        "verdict": verdict,
        "separated_at": separated["rung"] if separated else None,
        "rungs": rows,
        "rungs_refused": refused,
        "rungs_absent": [row["rung"] for row in rows if row["verdict"] == VERDICT_ABSENT],
        "cells_total": len(cells),
        "verdict_blocked_by": (
            [
                f"{row['rung']}/{row['route']}: {row['refusal']}"
                for row in rows
                if row["state"] == STATE_REFUSED
            ]
            or None
        ),
    }


def _grade_row(
    *,
    rung_id: str,
    route: str,
    heterogeneous: bool,
    present: Mapping[str, dict[str, Any]],
    margin_needed: int,
    drop_truncated: bool,
    hybrid_needs_mix: bool,
) -> dict[str, Any]:
    """One (rung, route) row: exclusions first, then the refusal, then a rule."""
    arms_seen = [arm for arm in ARM_ORDER if arm in present]
    arms_seen += [arm for arm in sorted(present) if arm not in ARM_ORDER]
    absent = [arm for arm in ARM_ORDER if arm not in present]

    cells: dict[str, dict[str, Any]] = {}
    for arm in ARM_ORDER:
        if arm not in present:
            cells[arm] = {"verdict": VERDICT_ABSENT, "state": STATE_UNRUN}
    for arm in arms_seen:
        raw = present[arm]
        entry: dict[str, Any] = {
            "verdict": None,
            "attempted": int(raw.get("attempted", 0)),
            "correct": int(raw.get("correct", 0)),
            "truncated_calls": int(raw.get("truncated_calls", 0)),
            "calls": int(raw.get("calls", 0)),
            "senses_config_hash": raw.get("senses_config_hash"),
        }
        if drop_truncated and entry["truncated_calls"]:
            entry["excluded"] = EXCLUSION_TRUNCATED
        elif hybrid_needs_mix and arm == ARM_HYBRID and not heterogeneous:
            entry["excluded"] = EXCLUSION_DEGENERATE
        cells[arm] = entry

    row: dict[str, Any] = {
        "rung": rung_id,
        "route": route,
        "heterogeneous": heterogeneous,
        "cells": cells,
        "cells_absent": absent,
        "leader": None,
        "margin": None,
        "graded_arms": [],
    }

    if not present:
        row.update({"state": STATE_UNRUN, "verdict": VERDICT_ABSENT})
        return row

    gradeable = [
        arm for arm in arms_seen if cells[arm].get("excluded") is None and arm in ARM_ORDER
    ]
    missing_controls = [arm for arm in FLAT_ARMS if arm not in gradeable]
    if missing_controls:
        reasons = []
        for arm in missing_controls:
            why = cells[arm].get("excluded") or "no cell was recorded"
            reasons.append(f"{arm} ({ARMS[arm].label}): {why}")
        row.update(
            {
                "state": STATE_REFUSED,
                "verdict": VERDICT_ABSENT,
                "refusal": (
                    "no verdict: this rung is missing a gradeable control — "
                    + "; ".join(reasons)
                    + ". An orchestration claim is only meaningful against BOTH flat arms."
                ),
            }
        )
        return row

    scores = {arm: cells[arm]["correct"] for arm in gradeable}
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    leader, best = ordered[0]
    runner_up = ordered[1][1] if len(ordered) > 1 else best
    margin = best - runner_up
    row.update(
        {
            "state": STATE_GRADED,
            "graded_arms": gradeable,
            "scores": scores,
            "leader": leader if margin >= margin_needed else None,
            "margin": margin,
            "verdict": (VERDICT_SEPARATED if margin >= margin_needed else VERDICT_INCONCLUSIVE),
        }
    )
    return row


# ── scripted minds (no network, no live model) ───────────────────────────────

#: What a scripted mind answers with. Deliberately not a correct answer to any
#: committed problem: the scripted lane is a WIRING check, and a scripted run
#: that scored well would be mistaken for data.
SCRIPTED_ANSWER = "SCRIPTED-NO-ANSWER"


def default_scripted_mind(
    role: str, tools: Optional[list[dict[str, Any]]]
) -> Callable[[list[dict[str, Any]]], ModelResponse]:
    """A mind that reads its own tool schema and behaves accordingly.

    Which is what a real model does. Deciding from the schema rather than from
    the arm id keeps the stand-in honest: it exercises whatever surface the
    harness actually put on the wire, so a surface bug shows up here rather than
    surviving to the live lane.
    """
    names = {entry["function"]["name"] for entry in (tools or [])}

    def complete(messages: list[dict[str, Any]]) -> ModelResponse:
        transcript = " ".join(str(message.get("content") or "") for message in messages)
        if "delegate" in names and DELEGATION_MARKER not in transcript:
            return ModelResponse(
                content="Handing the ground work to the worker.",
                reasoning="scripted",
                tool_calls=[
                    ToolCall(
                        id="call-delegate",
                        name="delegate",
                        arguments={
                            "subtask": "work the problem and report what you find",
                            "reason": "scripted stand-in: this arm delegates its ground work",
                        },
                    )
                ],
            )
        if WORKER_TERMINAL_TOOL in names:
            return ModelResponse(
                content="Reporting back.",
                reasoning="scripted",
                tool_calls=[
                    ToolCall(
                        id="call-report",
                        name=WORKER_TERMINAL_TOOL,
                        arguments={"findings": SCRIPTED_ANSWER},
                    )
                ],
            )
        if "finish" in names:
            return ModelResponse(
                content="Submitting.",
                reasoning="scripted",
                tool_calls=[
                    ToolCall(id="call-finish", name="finish", arguments={"answer": SCRIPTED_ANSWER})
                ],
            )
        return ModelResponse(content=SCRIPTED_ANSWER, reasoning="scripted", tool_calls=[])

    return complete


# ── CLI ──────────────────────────────────────────────────────────────────────


def render_plan(config: Optional[ArchConfig] = None) -> str:
    lines = ["arch_arms — four architectures over the challenge rungs", "", "arms:"]
    for arm_id in ARM_ORDER:
        arm = ARMS[arm_id]
        mark = "  (control)" if arm_id in FLAT_ARMS else ""
        lines.append(f"  {arm.id}  {arm.label:<12} {arm.shape:<13} top={arm.top_level_role}{mark}")
        lines.append(f"      {arm.why}")
    lines += [
        "",
        "controls: " + ", ".join(FLAT_ARMS) + " — analyse refuses a verdict without both",
        "",
        "ladder:",
    ]
    for rung in LADDER:
        mixed = "heterogeneous" if rung.heterogeneous(PROBLEMS) else "single-difficulty"
        lines.append(f"  {rung.id}  {', '.join(rung.problems)}  [{mixed}]")
        lines.append(f"      {rung.why}")
        if not rung.heterogeneous(PROBLEMS):
            lines.append(f"      -> arm {ARM_HYBRID} excluded from this rung's verdict by rule")
    lines += ["", "routes:"]
    for route in ROUTES:
        lines.append(f"  {route}: {ROUTE_WHY.get(route, '')}")
    if config is not None:
        lines += ["", f"sampling table: {config.path}", "", "sampling (temperature/thinking/max):"]
        for arm_id in ARM_ORDER:
            for role in ARMS[arm_id].configured_roles:
                cell = config.sampling_for(arm_id, role)
                lines.append(
                    f"  {arm_id}/{role:<7} t={cell.temperature} thinking={cell.thinking} "
                    f"max={cell.max_tokens}"
                )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--config", default=None, help="path to the sampling table")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="the arms, the ladder and the rules")
    plan.add_argument("--json", action="store_true")

    config_cmd = subparsers.add_parser("config", help="the sampling table as it was read")
    config_cmd.add_argument("--json", action="store_true")
    config_cmd.add_argument("--config", default=None, help="path to the sampling table")

    runner = subparsers.add_parser("run", help="run a rung (scripted unless --live)")
    runner.add_argument("--rung", default=None, help="rung id; default is the whole ladder")
    runner.add_argument("--arm", action="append", default=None, help="restrict to these arms")
    runner.add_argument("--out", default=None, help="write the JSONL artifact here")
    runner.add_argument("--live", action="store_true", help=f"dial the rig (needs {LIVE_GATE_ENV})")
    runner.add_argument("--config", default=None, help="path to the sampling table")

    analyser = subparsers.add_parser("analyse", help="re-apply the rule to an artifact")
    analyser.add_argument("--log", required=True)
    analyser.add_argument("--config", default=None, help="path to the sampling table")
    return parser


def _fail(message: str, hint: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    print(f"hint: {hint}", file=sys.stderr)
    return 2


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    config_path = Path(args.config) if getattr(args, "config", None) else None

    if args.command == "plan":
        if args.json:
            print(
                json.dumps(
                    {
                        "arms": {arm: ARMS[arm].to_dict() for arm in ARM_ORDER},
                        "flat_arms": list(FLAT_ARMS),
                        "ladder": [rung.to_dict(PROBLEMS) for rung in LADDER],
                        "routes": {route: ROUTE_WHY.get(route, "") for route in ROUTES},
                    },
                    indent=2,
                )
            )
        else:
            print(render_plan())
        return 0

    try:
        config = load_config(config_path)
    except ConfigError as broken:
        return _fail(
            str(broken), f"check the sampling table at {config_path or DEFAULT_CONFIG_PATH}"
        )

    if args.command == "config":
        payload = config.to_dict()
        try:
            payload["senses_config_hash"] = assert_senses_identical(config)
        except ConfigError as drifted:
            return _fail(str(drifted), "make every arm's senses cell identical, then re-run")
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(render_plan(config))
            print()
            print(f"senses_config_hash: {payload['senses_config_hash']}")
        return 0

    if args.command == "analyse":
        try:
            print(json.dumps(analyse(Path(args.log), config=config), indent=2, ensure_ascii=False))
        except OSError as unreadable:
            return _fail(str(unreadable), "point --log at a JSONL artifact this harness wrote")
        return 0

    if args.live:
        try:
            require_live_rig()
        except LiveRigClosed as shut:
            return _fail(
                str(shut),
                f"export {LIVE_GATE_ENV}=1 to dial the rig, or drop --live for the "
                "scripted lane (live runs are frozen for this cycle)",
            )
        return _fail(
            "the live lane is not dialled by this task",
            "task t12 runs the series; t5 ships the instrument and its scripted lane",
        )

    ladder = (LADDER_BY_ID[args.rung],) if args.rung else LADDER
    if args.rung and args.rung not in LADDER_BY_ID:
        return _fail(f"unknown rung {args.rung!r}", f"known rungs: {', '.join(LADDER_BY_ID)}")
    arms = tuple(args.arm) if args.arm else ARM_ORDER

    log = CallLog()
    seams = ScriptedSeams(config=config, log=log)
    try:
        report = run_series(
            config=config,
            seams=seams,
            log=log,
            ladder=ladder,
            arms=arms,
            out=Path(args.out) if args.out else None,
        )
    except ConfigError as broken:
        return _fail(str(broken), "fix the sampling table, then re-run")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
