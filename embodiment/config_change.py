"""The strategist's output unit: a typed CONFIGURATION change, not advisory prose.

This is the architectural core of the config-not-minds redesign (task ``t3``;
spec claims ``c2``/``h6``, ``c15``/``h3``, ``c29``/``h13``). The advisory
strategist produced versioned *prose* that crossed a boundary into the worker's
context and could be ignored — the one matched control on record measured it
buying nothing (189.6 s and 3663 tokens for zero applied directives and a
materially identical answer; n=1, one rig, one model pair, a report rather than
a measurement). The redesign replaces that with typed changes to the
configuration each seat runs under. The worker never sees prose from the
strategist, so there is no prose to obey, and *unawareness* — not persuasion —
is the mechanism.

Configuration authority is strictly stronger than directive authority. Advice
evaporates; configuration accumulates. Everything below is shaped by that.

The authority lattice (operator decisions, encoded as data)
------------------------------------------------------------
Seven targets, one typed dataclass each, and a matrix saying who may write
which. Read :data:`CHANGE_AUTHORITY` as the whole security story of the tier:

* the **strategist** may change the worker's tools, prompts, knowledge and
  permissions, and the senses seat's prompts, permissions and knowledge;
* the **worker** may write exactly one target — :data:`TARGET_SENSES_KNOWLEDGE`,
  the designated, schema-distinct block of what composes senses' context — and
  **never** a prompt. Prompt authority over senses belongs to the strategist
  alone. The lattice is read *narrowly* on purpose: the operator granted the
  worker the senses knowledge block and nothing else, and a lattice that grants
  more than was granted is minting authority in the place nobody would look.
  Widening it is an operator decision, not an edit;
* the **host** is the ground authority. It wired every capability in the first
  place, so it can write anything — which is also what makes ``t6``'s
  revert-to-baseline expressible as an ordinary change rather than as a
  privileged back door.

That the worker cannot reach a prompt is **structural, not conventional**, in
three layers that ``tests/test_config_change.py`` pins separately: the lattice
refuses the ``(origin, target)`` pair whole; no worker-writable unit is a
:class:`PromptChange` subclass; and no worker-writable unit has a field prompt
text could land in.

Refuse whole, never strip-and-keep — cited from scope.py, not imported
----------------------------------------------------------------------
The pattern is ``embodiment/scope.py``'s ``directive_from_payload``
(scope.py:1288-1314) and its ``FORBIDDEN_DIRECTIVE_KEYS`` walk, reused **by
citation**. This module imports nothing from ``scope.py``, ``scoped_run.py``,
``strategist_runner.py`` or ``loop.py``, and a test proves it: the advisory lane
has to stay byte-stable because it is the comparator arm the config-change arm
will be measured against, and an import edge is a reason to edit it.

What is copied is the *discipline*, and scope.py's own reasoning for it applies
here with more force: stripping an offending key and honouring the rest would
let the attempt succeed at the part that mattered, and would leave a strategist
probing the boundary with no cost for doing so.

What is deliberately **stricter** than scope.py: this lane refuses an *unknown*
key, not only a forbidden one. ``ScopeDirective.from_dict`` ignores unknown keys
so a payload written by a later release still reads back in an older build —
the right trade for a directive, whose worst case is scope nobody acts on. A
configuration unit's worst case is a seat running under settings nobody
authored, so the vocabulary is closed: :func:`declared_keys` is the whole of it.

The two refusals stay separate codes because they have different fixes.
:data:`CHANGE_UNKNOWN_KEY` means *you sent a field this schema does not have*;
:data:`CHANGE_AUTHORITY_VIOLATION` means *you tried to reach past configuration
authority into action authority*. The forbidden-key walk runs first and
descends to depth, so a smuggled ``command`` nested inside a declared field is
recorded as what it is rather than as a typo.

The ban is on **keys**, not on prose — scope.py's rule, inherited verbatim. A
prompt change whose ``text`` reads "run the full test suite before you finish"
is exactly what a prompt change is for.

Selection, never minting (honesty condition ``h11``)
-----------------------------------------------------
A tools or permissions unit carries **capability ids and nothing else**, and
every id must be declared in the host's :class:`~embodiment.capability.CapabilityCatalog`.
With no catalog supplied the unit is refused (:data:`CHANGE_NO_CATALOG`):
absence of a declaration is never read as permission. A free-form tool
definition is a refused *shape* twice over — there is no field one could land in
(:class:`CapabilitySelection` declares ``capability_ids`` alone) and
``definition`` / ``schema`` / ``parameters`` are forbidden keys.

Read ``embodiment/capability.py``'s module docstring for the enumeration
decision itself (plan risk ``r2``) — how ids come to exist, why they are host
declarations rather than anything discovered from a ``ToolExecutor``, and how a
catalog that moves under a pending change is detected (:func:`revalidate`).

A selection is a **set, not a delta**: ``capability_ids`` is the seat's whole
surface after the change, not an addition to it. Deltas compound invisibly, and
compounding is precisely what this cycle's ratchet requirement (``c7``) exists
to make detectable — a full selection makes "revert to baseline" a single
ordinary change rather than an inferred inverse.

Where the rest of the tier lands
--------------------------------
This module is schemas and admission only: no thread, no clock, no IO, no store,
no apply. Deliberately, and for the same reason ``scope.py`` splits from
``strategist_runner.py`` — all the reasoning is provable before anything runs in
parallel. The rest of the tier lands in its own files so a fan-out does not
collide: propose → verify → apply is task ``t4``'s, the ledger, events and
fail-closed persistence are ``t5``'s, revert and ratchet are ``t6``'s,
ledger-derived introspection is ``t7``'s, and the knowledge block's eidetic
composition is ``t8``'s. Nothing here writes anything anywhere.

Both modules are registered as reachable submodules (``from embodiment import
config_change``) but **no name is hoisted into the package's lazy re-export
map**. That is the muse-archival distinction applied forward rather than
backward: ``_LAZY_NAMES`` membership is *advertisement*, and this tier ships
opt-in and off with its value unmeasured until the ScopeBench re-run (``t13`` /
``t14``) says otherwise. Hoisting waits for a verdict; reach does not.

Every refusal is RECORDED, never dropped
-----------------------------------------
:class:`ConfigRefusal` **is a** :class:`ConfigDegradation` (it subclasses it),
exactly as ``ScopeRejection`` is a ``ScopeDegradation``, so ``t5``'s ledger
folds ONE stream and a host counting degradations cannot miss a refusal by
looking in the wrong list. The four field names and ``to_dict`` keys
``ScopeDegradation`` carries are carried here too, so the ledger reader needs no
second shape. :func:`admit_changes` makes the guarantee countable: every payload
offered comes back either accepted or refused, and the totals conserve.

Stdlib only, plus :mod:`embodiment.capability`: ``dataclasses`` and ``typing``.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, ClassVar, Optional, Sequence

from embodiment.capability import (
    CAPABILITY_KIND_PERMISSION,
    CAPABILITY_KIND_TOOL,
    CapabilityCatalog,
)

__all__ = [
    # seats and targets
    "SEAT_WORKER",
    "SEAT_SENSES",
    "CHANGE_SEATS",
    "TARGET_WORKER_TOOLS",
    "TARGET_WORKER_PROMPTS",
    "TARGET_WORKER_KNOWLEDGE",
    "TARGET_WORKER_PERMISSIONS",
    "TARGET_SENSES_PROMPTS",
    "TARGET_SENSES_PERMISSIONS",
    "TARGET_SENSES_KNOWLEDGE",
    "CHANGE_TARGETS",
    "PROMPT_TARGETS",
    "CAPABILITY_TARGETS",
    "KNOWLEDGE_TARGETS",
    # origins and the authority lattice
    "ORIGIN_STRATEGIST",
    "ORIGIN_WORKER",
    "ORIGIN_HOST",
    "CHANGE_ORIGINS",
    "CHANGE_AUTHORITY",
    # the key ban
    "FORBIDDEN_CHANGE_KEYS",
    # refusal vocabulary (C3)
    "CHANGE_MALFORMED",
    "CHANGE_UNKNOWN_TARGET",
    "CHANGE_UNKNOWN_KEY",
    "CHANGE_AUTHORITY_VIOLATION",
    "CHANGE_NO_ORIGIN",
    "CHANGE_ORIGIN_FORBIDDEN",
    "CHANGE_INCOMPLETE",
    "CHANGE_NO_CATALOG",
    "CHANGE_UNKNOWN_CAPABILITY",
    "CHANGE_STALE_CATALOG",
    "CHANGE_REFUSAL_CODES",
    # shapes
    "ConfigDegradation",
    "ConfigRefusal",
    "ConfigChange",
    "PromptChange",
    "KnowledgeChange",
    "CapabilitySelection",
    "WorkerToolsChange",
    "WorkerPromptChange",
    "WorkerKnowledgeChange",
    "WorkerPermissionsChange",
    "SensesPromptChange",
    "SensesPermissionsChange",
    "SensesKnowledgeChange",
    "CHANGE_UNITS",
    "ChangeAdmission",
    # admission
    "declared_keys",
    "change_from_payload",
    "admit_changes",
    "revalidate",
]


# ── seats and targets ─────────────────────────────────────────────────────────
#
# A target is ``<seat>.<surface>``. The seat is *derived* from the target rather
# than declared beside it, so a unit cannot carry a seat that disagrees with its
# own target — one fact, one place.

#: The acting seat: the loop that writes code and calls tools.
SEAT_WORKER = "worker"
#: The relay seat: what the operator hears and is heard by.
SEAT_SENSES = "senses"
#: The two seats this lattice configures. The strategist configures; it is not
#: itself a configurable seat here (a tier that reconfigures itself is a ratchet
#: with no fixed baseline, which is exactly what ``c7`` forbids).
CHANGE_SEATS = (SEAT_WORKER, SEAT_SENSES)

#: Which of the host's declared tool capabilities the worker's surface is.
TARGET_WORKER_TOOLS = "worker.tools"
#: The worker's prompt text, by named section.
TARGET_WORKER_PROMPTS = "worker.prompts"
#: The worker's knowledge block — attributed claims, not the seat's perception.
TARGET_WORKER_KNOWLEDGE = "worker.knowledge"
#: Which of the host's declared permission capabilities the worker holds.
TARGET_WORKER_PERMISSIONS = "worker.permissions"
#: The senses seat's prompt text. **Strategist-only** — see :data:`CHANGE_AUTHORITY`.
TARGET_SENSES_PROMPTS = "senses.prompts"
#: Which of the host's declared permission capabilities the senses seat holds.
TARGET_SENSES_PERMISSIONS = "senses.permissions"
#: The senses knowledge block: the designated, schema-distinct surface the
#: worker may write. Arguably part of the prompt surface, deliberately
#: designated as its own so that "the worker writes knowledge, never prompts" is
#: a statement about two different schemas rather than about two conventions.
TARGET_SENSES_KNOWLEDGE = "senses.knowledge"

#: Every target, in the operator's own order (worker first, then senses).
CHANGE_TARGETS = (
    TARGET_WORKER_TOOLS,
    TARGET_WORKER_PROMPTS,
    TARGET_WORKER_KNOWLEDGE,
    TARGET_WORKER_PERMISSIONS,
    TARGET_SENSES_PROMPTS,
    TARGET_SENSES_PERMISSIONS,
    TARGET_SENSES_KNOWLEDGE,
)

#: The prompt-shaped targets — the ones the worker can never reach.
PROMPT_TARGETS = (TARGET_WORKER_PROMPTS, TARGET_SENSES_PROMPTS)
#: The capability-shaped targets — selection only, never minting.
CAPABILITY_TARGETS = (
    TARGET_WORKER_TOOLS,
    TARGET_WORKER_PERMISSIONS,
    TARGET_SENSES_PERMISSIONS,
)
#: The knowledge-shaped targets — attributed claims, eidetic-backed (task t8).
KNOWLEDGE_TARGETS = (TARGET_WORKER_KNOWLEDGE, TARGET_SENSES_KNOWLEDGE)


# ── origins and the authority lattice ─────────────────────────────────────────

#: The strategist tier — the cortex reviewing in the background.
ORIGIN_STRATEGIST = "strategist"
#: The acting loop. It may write the senses knowledge block and nothing else.
ORIGIN_WORKER = "worker"
#: The host itself — baselines, and ``t6``'s revert-to-baseline.
ORIGIN_HOST = "host"
#: The closed set. An origin outside it is unattributed (:data:`CHANGE_NO_ORIGIN`).
CHANGE_ORIGINS = (ORIGIN_STRATEGIST, ORIGIN_WORKER, ORIGIN_HOST)

#: Who may write what. The operator's lattice, as data rather than as prose.
#:
#: The worker's single entry is the load-bearing one and it is deliberately the
#: narrowest reading of what was granted: the senses knowledge block, because
#: the worker-to-senses channel is a path from the acting tier to the operator's
#: ear and it has to be attributed (``c30``) — never a prompt, and never a
#: capability, because a seat that can widen its own tool surface is the
#: escalation ``h11`` exists to prevent.
CHANGE_AUTHORITY: dict[str, frozenset[str]] = {
    ORIGIN_STRATEGIST: frozenset(CHANGE_TARGETS),
    ORIGIN_WORKER: frozenset({TARGET_SENSES_KNOWLEDGE}),
    ORIGIN_HOST: frozenset(CHANGE_TARGETS),
}


# ── the key ban ───────────────────────────────────────────────────────────────

#: Keys a change payload may never carry, at any depth. Matched
#: case-insensitively, after stripping.
#:
#: Related to ``scope.py``'s ``FORBIDDEN_DIRECTIVE_KEYS`` but **not** the same
#: list and deliberately not imported from it: a directive banned anything
#: action-shaped, while a configuration unit is *allowed* to name tools and
#: permissions — that is the whole point of the redesign (``q2``: the ban on
#: ``tool``/``allow``/``deny`` as directive data is what this tier retires). What
#: it bans instead is one class of thing: **content that would execute, or that
#: would define a capability rather than select one**. A change unit is data
#: applied to a context; nothing in it is ever run.
#:
#: These are **keys**, not words. A prompt whose text reads "run the tests" is a
#: legitimate prompt change; the seat treating that prose as an instruction is
#: what prompts are *for*.
FORBIDDEN_CHANGE_KEYS = (
    # executable content — nothing a change unit carries is ever run
    "command",
    "commands",
    "cmd",
    "shell",
    "exec",
    "execute",
    "run",
    "script",
    "code",
    "python",
    "eval",
    "entrypoint",
    "argv",
    "subprocess",
    # capability DEFINITION — h11: select among host-declared ids, never mint
    "definition",
    "definitions",
    "schema",
    "parameters",
    "function",
    "functions",
    "implementation",
    "handler",
    "callable",
    "endpoint",
    "url",
    # approval decisions — the host's approval policy is never bypassed
    "approve",
    "approved",
    "approval",
    "approvals",
    "deny",
    "denied",
    "allow",
    "allowed",
    "permit",
    "veto",
    "bypass",
    "override",
    "sudo",
    "escalate",
    # write surfaces — a change unit configures a seat, it does not edit a repo
    "patch",
    "diff",
    "edit",
    "edits",
    "file_edits",
    "filename",
    "filepath",
)


# ── refusal vocabulary (C3) ───────────────────────────────────────────────────
#
# Every code is prefixed ``config-change-`` so task t5's ledger lane harvests a
# vocabulary that cannot collide with the advisory lane's ``scope-`` codes. One
# code per distinct FIX — that is the principle scope.py's five refusal codes
# follow, and it is why a worker reaching a prompt and a worker reaching a tool
# share one code (the fix is the same: author it from the seat that owns it).
# Every one has a producer in this file and a test that fires it: embodiment#18's
# lesson is that a code nothing can mint is a lie in the ledger.

#: The payload was not a mapping, or could not be read as one.
CHANGE_MALFORMED = "config-change-unreadable"
#: It named no target, or a target outside :data:`CHANGE_TARGETS`.
CHANGE_UNKNOWN_TARGET = "config-change-unknown-target"
#: It carried a key this target's schema does not declare. Refused WHOLE.
CHANGE_UNKNOWN_KEY = "config-change-unknown-key"
#: It carried a :data:`FORBIDDEN_CHANGE_KEYS` key at some depth — a reach past
#: configuration authority into action authority. Refused WHOLE.
CHANGE_AUTHORITY_VIOLATION = "config-change-authority-violation"
#: It named no origin, or an origin outside :data:`CHANGE_ORIGINS`. An
#: unattributed write is a refused shape (``c30``/``h20``): the worker-to-senses
#: channel reaches the operator's ear, and an anonymous channel there would let
#: a fabricating acting tier put words in the interaction tier's mouth.
CHANGE_NO_ORIGIN = "config-change-unattributed"
#: Its origin is real but does not own its target (:data:`CHANGE_AUTHORITY`).
CHANGE_ORIGIN_FORBIDDEN = "config-change-origin-forbidden"
#: A required field of this target's schema was blank — it changes nothing, or
#: names nothing the ledger could revert.
CHANGE_INCOMPLETE = "config-change-incomplete"
#: A capability-shaped unit was offered with no host catalog to select from.
#: Fail-closed: absence of a declaration is never read as permission.
CHANGE_NO_CATALOG = "config-change-no-catalog"
#: It named a capability id the host's catalog does not declare, or declares
#: under a different kind. The whole unit is refused, not the offending id.
CHANGE_UNKNOWN_CAPABILITY = "config-change-unknown-capability"
#: The host's catalog moved between validation and re-validation — a different
#: catalog, or the same one with a different content fingerprint. ``drone.py``'s
#: staleness refusal, moved to runtime.
CHANGE_STALE_CATALOG = "config-change-stale-catalog"

#: The complete refusal set. Every one is a recorded :class:`ConfigRefusal`.
CHANGE_REFUSAL_CODES = (
    CHANGE_MALFORMED,
    CHANGE_UNKNOWN_TARGET,
    CHANGE_UNKNOWN_KEY,
    CHANGE_AUTHORITY_VIOLATION,
    CHANGE_NO_ORIGIN,
    CHANGE_ORIGIN_FORBIDDEN,
    CHANGE_INCOMPLETE,
    CHANGE_NO_CATALOG,
    CHANGE_UNKNOWN_CAPABILITY,
    CHANGE_STALE_CATALOG,
)


# ── bounds ────────────────────────────────────────────────────────────────────

#: Cap on a recorded reason's text, so a runaway payload cannot blow up a host's
#: artifact. Mirrors ``scope.py`` and ``muse.py``.
_MAX_REASON_LEN = 500
#: How deep the forbidden-key walk descends. A bound, not a judgement: a payload
#: nested deeper than this is not a change-unit shape at all. ``scope.py``'s
#: value, for the same reason.
_MAX_PAYLOAD_DEPTH = 8
#: Fields a validation STAMPS onto an accepted unit. They are provenance, never
#: model-supplied, so they are excluded from :func:`declared_keys` — which makes
#: a payload that tries to supply one an ordinary unknown key, refused whole.
_STAMPED = ("catalog_id", "catalog_fingerprint")


# ── coercion helpers (never raise) ────────────────────────────────────────────


def _text(value: Any) -> str:
    """Coerce *value* to text. Never raises — ``scope.py``'s ``_plain``, cited."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return str(value)
    except Exception:  # noqa: BLE001  # an unreadable value is a blank, never a crash
        return ""


def _optional_text(value: Any) -> Optional[str]:
    """``None`` stays ``None``; everything else becomes text."""
    if value is None:
        return None
    return _text(value)


def _as_ids(value: Any) -> tuple[str, ...]:
    """Coerce a raw capability-id payload. Never raises.

    A bare string becomes a one-element tuple rather than a tuple of characters
    (``contract._coerce_omissions``' lesson, inherited through ``scope.py``).
    Nothing is dropped: an entry that strips to empty stays, and is refused by
    the catalog check as the undeclared id it is. Silently dropping it would
    make a malformed selection look like a deliberate one.
    """
    if value is None:
        return ()
    if isinstance(value, str):
        return (value.strip(),)
    if isinstance(value, (list, tuple)):
        return tuple(_text(entry).strip() for entry in value)
    return (_text(value).strip(),)


# ── the recorded shapes ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class ConfigDegradation:
    """One recorded, host-visible configuration degradation (constraint C3).

    Carries :class:`embodiment.scope.ScopeDegradation`'s first four fields under
    the same names and the same ``to_dict`` keys — which are in turn
    ``embodiment.loop.LoopDegradation``'s — so ``t5``'s ledger reader folds ONE
    shape rather than three more. It is not imported from either: this lane
    consumes neither the actor loop nor the advisory one, and importing would
    drag their vocabularies into a module that must not have them in scope.

    ``seat`` and ``target`` are this lane's own two: a configuration record that
    cannot say which seat it was about is not a record.
    """

    code: str
    reason: str
    step_index: int = 0
    model_turns: int = 0
    seat: str = ""
    target: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "reason": self.reason,
            "step_index": self.step_index,
            "model_turns": self.model_turns,
            "seat": self.seat,
            "target": self.target,
        }


@dataclass(frozen=True)
class ConfigRefusal(ConfigDegradation):
    """A change unit that was offered and then REFUSED — recorded, never dropped.

    Subclasses :class:`ConfigDegradation` rather than paralleling it, exactly as
    ``ScopeRejection`` subclasses ``ScopeDegradation`` and for the identical
    reason: a host counting degradations must not be able to miss a refusal by
    looking in the wrong list.

    It adds the identity of what was turned away, read defensively from the raw
    payload — so a refusal is attributable even when the payload was too
    malformed to build a unit from. ``code`` is one of
    :data:`CHANGE_REFUSAL_CODES`.
    """

    change_id: str = ""
    origin: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data.update({"change_id": self.change_id, "origin": self.origin})
        return data


# ── the change units: one typed dataclass per target ──────────────────────────


@dataclass(frozen=True)
class ConfigChange:
    """The envelope every change unit carries. Never a target in its own right.

    :attr:`TARGET` is a ``ClassVar``, so it is the *type's* identity rather than
    a field a payload could set — a unit cannot claim to be aimed somewhere its
    class is not, and :attr:`seat` is derived from it rather than declared
    beside it.

    Fields
    ------
    change_id:
        Required. The ledger identity. A change that names nothing cannot be
        cited in a provenance report or reverted — ``scope.py``'s reason for
        requiring ``scope_id``, applied to a stronger authority.
    origin:
        Required, and one of :data:`CHANGE_ORIGINS`. The attribution the
        authority lattice is enforced against.
    reason:
        Why the change was made — legible, and explicitly not a transcript of
        reasoning. Optional: a host's baseline has no argument to make.
    """

    change_id: str = ""
    origin: str = ""
    reason: str = ""

    #: The target this class is the unit for. Empty on the envelope and on every
    #: shape-family base; set on exactly the seven concrete units.
    TARGET: ClassVar[str] = ""

    def __post_init__(self) -> None:
        for name in ("change_id", "origin", "reason"):
            object.__setattr__(self, name, _text(getattr(self, name)).strip())

    @property
    def target(self) -> str:
        """This unit's target — the class's identity, not a field."""
        return self.TARGET

    @property
    def seat(self) -> str:
        """The seat this change configures, derived from :attr:`target`."""
        return self.TARGET.split(".")[0] if self.TARGET else ""

    def to_dict(self) -> dict[str, Any]:
        """A JSON-ready payload. ``target`` and ``seat`` are rendered, not stored."""
        data: dict[str, Any] = {
            "target": self.target,
            "seat": self.seat,
            "change_id": self.change_id,
            "origin": self.origin,
            "reason": self.reason,
        }
        for entry in fields(self):
            if entry.name in ("change_id", "origin", "reason"):
                continue
            value = getattr(self, entry.name)
            data[entry.name] = list(value) if isinstance(value, tuple) else value
        return data


@dataclass(frozen=True)
class PromptChange(ConfigChange):
    """A change to one named section of a seat's prompt. Strategist-only, always.

    ``section`` names *what* is being changed and is required: a prompt change
    that cannot say which section it replaced cannot be reverted. ``text`` may be
    empty — clearing a section is a real change, and revert has to be
    expressible without a second verb.
    """

    section: str = ""
    text: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        object.__setattr__(self, "section", _text(self.section).strip())
        object.__setattr__(self, "text", _text(self.text))


@dataclass(frozen=True)
class KnowledgeChange(ConfigChange):
    """One attributed claim in a seat's knowledge block.

    Knowledge is *attributed claims*, never the seat's own perception — the
    grounding clause a host ships beside senses says so, and
    :attr:`ConfigChange.origin` is what carries the attribution. ``supersedes``
    names the entry this one replaces, which is where ``t8``'s eidetic
    composition hangs: eidetic owns the memory mechanics (consolidation,
    supersession, ageing), embodiment owns *when* an entry is written.
    """

    entry_id: str = ""
    text: str = ""
    supersedes: Optional[str] = None

    def __post_init__(self) -> None:
        super().__post_init__()
        object.__setattr__(self, "entry_id", _text(self.entry_id).strip())
        object.__setattr__(self, "text", _text(self.text))
        object.__setattr__(self, "supersedes", _optional_text(self.supersedes))


@dataclass(frozen=True)
class CapabilitySelection(ConfigChange):
    """A selection among the host's declared capability ids. Never a definition.

    ``capability_ids`` is the seat's **whole** surface after the change, not a
    delta — see the module docstring on why a set beats a delta for ``c7``'s
    ratchet condition.

    ``catalog_id`` and ``catalog_fingerprint`` are **stamped** by
    :func:`change_from_payload` from the catalog the unit validated against, and
    are excluded from :func:`declared_keys` so a payload cannot supply them.
    They are what :func:`revalidate` re-checks before ``t4`` applies anything.
    """

    capability_ids: tuple[str, ...] = ()
    catalog_id: str = ""
    catalog_fingerprint: str = ""

    #: Which kind of capability this target selects. One of
    #: :data:`~embodiment.capability.CAPABILITY_KINDS`.
    KIND: ClassVar[str] = CAPABILITY_KIND_TOOL

    def __post_init__(self) -> None:
        super().__post_init__()
        object.__setattr__(self, "capability_ids", _as_ids(self.capability_ids))
        object.__setattr__(self, "catalog_id", _text(self.catalog_id).strip())
        object.__setattr__(self, "catalog_fingerprint", _text(self.catalog_fingerprint).strip())


@dataclass(frozen=True)
class WorkerToolsChange(CapabilitySelection):
    """Which host-declared tools the worker may call."""

    TARGET: ClassVar[str] = TARGET_WORKER_TOOLS
    KIND: ClassVar[str] = CAPABILITY_KIND_TOOL


@dataclass(frozen=True)
class WorkerPermissionsChange(CapabilitySelection):
    """Which host-declared permissions the worker holds."""

    TARGET: ClassVar[str] = TARGET_WORKER_PERMISSIONS
    KIND: ClassVar[str] = CAPABILITY_KIND_PERMISSION


@dataclass(frozen=True)
class SensesPermissionsChange(CapabilitySelection):
    """Which host-declared permissions the senses seat holds."""

    TARGET: ClassVar[str] = TARGET_SENSES_PERMISSIONS
    KIND: ClassVar[str] = CAPABILITY_KIND_PERMISSION


@dataclass(frozen=True)
class WorkerPromptChange(PromptChange):
    """One named section of the worker's prompt."""

    TARGET: ClassVar[str] = TARGET_WORKER_PROMPTS


@dataclass(frozen=True)
class SensesPromptChange(PromptChange):
    """One named section of the senses seat's prompt. Unreachable from the worker."""

    TARGET: ClassVar[str] = TARGET_SENSES_PROMPTS


@dataclass(frozen=True)
class WorkerKnowledgeChange(KnowledgeChange):
    """One attributed claim in the worker's knowledge block."""

    TARGET: ClassVar[str] = TARGET_WORKER_KNOWLEDGE


@dataclass(frozen=True)
class SensesKnowledgeChange(KnowledgeChange):
    """One attributed claim in the senses knowledge block — the worker's one target."""

    TARGET: ClassVar[str] = TARGET_SENSES_KNOWLEDGE


#: Target → the one dataclass that is its unit. The dispatch table, and the
#: proof that "one typed dataclass per target" is a fact rather than a claim.
CHANGE_UNITS: dict[str, type[ConfigChange]] = {
    TARGET_WORKER_TOOLS: WorkerToolsChange,
    TARGET_WORKER_PROMPTS: WorkerPromptChange,
    TARGET_WORKER_KNOWLEDGE: WorkerKnowledgeChange,
    TARGET_WORKER_PERMISSIONS: WorkerPermissionsChange,
    TARGET_SENSES_PROMPTS: SensesPromptChange,
    TARGET_SENSES_PERMISSIONS: SensesPermissionsChange,
    TARGET_SENSES_KNOWLEDGE: SensesKnowledgeChange,
}


@dataclass(frozen=True)
class ChangeAdmission:
    """What one batch of offered payloads produced: what was taken, what was not.

    ``len(accepted) + len(refusals) == len(offered)`` is the countable form of
    "nothing is silently dropped", and ``tests/test_config_change.py`` asserts
    it. A host that wants the refusals in a ledger hands ``refusals`` to ``t5``;
    a host that ignores them has made a visible choice rather than an invisible
    one.
    """

    accepted: tuple[ConfigChange, ...] = ()
    refusals: tuple[ConfigRefusal, ...] = ()


# ── the forbidden-key walk (scope.py:1238-1285, cited) ────────────────────────


def _forbidden_in_mapping(payload: dict[Any, Any], depth: int) -> Optional[str]:
    """The first forbidden key at or under *payload*, its own keys first.

    The traversal order is part of the contract, not an accident: each key is
    judged, and only then is its own value descended into, so the *reported* key
    is the first one a reader of the payload would reach.
    """
    for key, value in payload.items():
        if _text(key).strip().lower() in FORBIDDEN_CHANGE_KEYS:
            return _text(key)
        found = _forbidden_key(value, depth + 1)
        if found is not None:
            return found
    return None


def _forbidden_in_sequence(entries: Sequence[Any], depth: int) -> Optional[str]:
    """The first forbidden key under any entry of *entries*, in order."""
    for entry in entries:
        found = _forbidden_key(entry, depth + 1)
        if found is not None:
            return found
    return None


def _forbidden_key(payload: Any, depth: int = 0) -> Optional[str]:
    """The first :data:`FORBIDDEN_CHANGE_KEYS` key in *payload*, at any depth.

    Case-insensitive, bounded by :data:`_MAX_PAYLOAD_DEPTH`. Never raises.
    """
    if depth > _MAX_PAYLOAD_DEPTH:
        return None
    if isinstance(payload, dict):
        return _forbidden_in_mapping(payload, depth)
    if isinstance(payload, (list, tuple)):
        return _forbidden_in_sequence(payload, depth)
    return None


# ── admission ─────────────────────────────────────────────────────────────────


def declared_keys(target: Any) -> tuple[str, ...]:
    """Every key a payload for *target* may carry — the whole closed vocabulary.

    ``target`` first (it selects the schema), then the unit's own fields in
    declaration order, minus :data:`_STAMPED`. Derived from
    ``dataclasses.fields`` rather than written out, so a field added by a later
    task is declared automatically instead of silently refusing every payload
    that uses it. An unknown target has no vocabulary at all.
    """
    unit = CHANGE_UNITS.get(_text(target).strip())
    if unit is None:
        return ()
    return ("target",) + tuple(entry.name for entry in fields(unit) if entry.name not in _STAMPED)


def _refuse(
    code: str,
    reason: str,
    *,
    payload: Any = None,
    target: str = "",
    origin: str = "",
    change_id: str = "",
) -> ConfigRefusal:
    """Mint one refusal, reading identity defensively from *payload* when given."""
    if isinstance(payload, dict):
        target = target or _text(payload.get("target")).strip()
        origin = origin or _text(payload.get("origin")).strip()
        change_id = change_id or _text(payload.get("change_id")).strip()
    seat = target.split(".")[0] if target in CHANGE_TARGETS else ""
    return ConfigRefusal(
        code=code,
        reason=reason[:_MAX_REASON_LEN],
        seat=seat,
        target=target,
        change_id=change_id,
        origin=origin,
    )


def _build(unit: type[ConfigChange], payload: dict[str, Any]) -> ConfigChange:
    """Construct one unit from a payload whose vocabulary is already closed."""
    kwargs = {
        entry.name: payload[entry.name]
        for entry in fields(unit)
        if entry.name not in _STAMPED and entry.name in payload
    }
    return unit(**kwargs)


def _incomplete(change: ConfigChange) -> Optional[str]:
    """The name of the first required field this unit left blank, or ``None``."""
    if not change.change_id:
        return "change_id"
    if isinstance(change, PromptChange) and not change.section:
        return "section"
    if isinstance(change, KnowledgeChange):
        if not change.entry_id:
            return "entry_id"
        if not change.text:
            return "text"
    return None


def _check_capabilities(
    change: CapabilitySelection, catalog: Optional[CapabilityCatalog]
) -> Optional[ConfigRefusal]:
    """Refuse a selection the host's declaration does not cover. Fail-closed."""
    if not isinstance(catalog, CapabilityCatalog):
        return _refuse(
            CHANGE_NO_CATALOG,
            f"the unit selects {change.KIND} capabilities but no host capability "
            "catalog was supplied; with nothing declared there is nothing to select, "
            "so the whole unit was refused",
            target=change.target,
            origin=change.origin,
            change_id=change.change_id,
        )
    undeclared = [
        name for name in change.capability_ids if not catalog.declares(name, kind=change.KIND)
    ]
    if undeclared:
        return _refuse(
            CHANGE_UNKNOWN_CAPABILITY,
            f"the host catalog {catalog.catalog_id or '(unnamed)'} declares no "
            f"{change.KIND} named {undeclared[0]!r}"
            + (f" (and {len(undeclared) - 1} more)" if len(undeclared) > 1 else "")
            + "; configuration selects among host-declared capabilities and never "
            "mints one, so the whole unit was refused",
            target=change.target,
            origin=change.origin,
            change_id=change.change_id,
        )
    return None


def change_from_payload(
    payload: Any,
    *,
    catalog: Optional[CapabilityCatalog] = None,
) -> tuple[Optional[ConfigChange], Optional[ConfigRefusal]]:
    """Read one raw payload as a typed change unit. ``(change, refusal)``; never raises.

    Exactly one of the two is ``None``. Every refusal path returns a
    :class:`ConfigRefusal` — nothing is dropped, and nothing is partially
    applied: a payload that fails any check is refused **whole**.

    The checks run in this order, and the order is part of the contract:

    1. the payload is a mapping (:data:`CHANGE_MALFORMED`);
    2. no :data:`FORBIDDEN_CHANGE_KEYS` key at any depth
       (:data:`CHANGE_AUTHORITY_VIOLATION`) — **first**, so a smuggled
       ``command`` is recorded as a reach for action authority rather than as an
       unknown field;
    3. the target is one of :data:`CHANGE_TARGETS` (:data:`CHANGE_UNKNOWN_TARGET`);
    4. every key is declared for that target (:data:`CHANGE_UNKNOWN_KEY`);
    5. the origin is one of :data:`CHANGE_ORIGINS` (:data:`CHANGE_NO_ORIGIN`);
    6. the origin owns the target (:data:`CHANGE_ORIGIN_FORBIDDEN`) — this is
       where a worker-originated prompt write dies;
    7. required fields are present (:data:`CHANGE_INCOMPLETE`);
    8. every capability id is host-declared (:data:`CHANGE_NO_CATALOG`,
       :data:`CHANGE_UNKNOWN_CAPABILITY`), after which the catalog's identity and
       fingerprint are stamped onto the accepted unit.

    Args:
        payload: the raw unit, as a mapping.
        catalog: the host's capability declaration. Required for a
            capability-shaped target, ignored for every other.
    """
    if not isinstance(payload, dict):
        return None, _refuse(
            CHANGE_MALFORMED,
            f"a change unit must be a mapping; got {type(payload).__name__}",
        )

    forbidden = _forbidden_key(payload)
    if forbidden is not None:
        return None, _refuse(
            CHANGE_AUTHORITY_VIOLATION,
            f"the change payload carried the key {forbidden!r}, which reaches past "
            "configuration authority into action authority; the whole unit was refused",
            payload=payload,
        )

    target = _text(payload.get("target")).strip()
    unit = CHANGE_UNITS.get(target)
    if unit is None:
        return None, _refuse(
            CHANGE_UNKNOWN_TARGET,
            f"{target!r} is not a configurable target; the known targets are "
            f"{', '.join(CHANGE_TARGETS)}",
            payload=payload,
            target=target,
        )

    allowed = declared_keys(target)
    for key in payload:
        if _text(key).strip() not in allowed:
            return None, _refuse(
                CHANGE_UNKNOWN_KEY,
                f"the {target} schema does not declare the key {_text(key)!r}; its "
                f"whole vocabulary is {', '.join(allowed)}, and a unit carrying "
                "anything else is refused whole rather than stripped",
                payload=payload,
            )

    origin = _text(payload.get("origin")).strip()
    if origin not in CHANGE_ORIGINS:
        return None, _refuse(
            CHANGE_NO_ORIGIN,
            f"the unit named the origin {origin!r}; a configuration change must be "
            f"attributed to one of {', '.join(CHANGE_ORIGINS)}, so the whole unit "
            "was refused",
            payload=payload,
        )

    if target not in CHANGE_AUTHORITY[origin]:
        return None, _refuse(
            CHANGE_ORIGIN_FORBIDDEN,
            f"{origin!r} does not own {target!r}; that origin may write "
            f"{', '.join(sorted(CHANGE_AUTHORITY[origin])) or 'nothing'}, so the "
            "whole unit was refused",
            payload=payload,
        )

    change = _build(unit, payload)

    missing = _incomplete(change)
    if missing is not None:
        return None, _refuse(
            CHANGE_INCOMPLETE,
            f"the {target} unit left {missing!r} blank; a change that cannot name "
            "what it changes cannot be recorded, reverted or explained",
            payload=payload,
        )

    if isinstance(change, CapabilitySelection):
        refusal = _check_capabilities(change, catalog)
        if refusal is not None:
            return None, refusal
        assert isinstance(catalog, CapabilityCatalog)  # nosec B101 - narrowed just above
        change = _stamp(change, catalog)

    return change, None


def _stamp(change: CapabilitySelection, catalog: CapabilityCatalog) -> CapabilitySelection:
    """Record which catalog this selection was validated against."""
    return type(change)(
        change_id=change.change_id,
        origin=change.origin,
        reason=change.reason,
        capability_ids=change.capability_ids,
        catalog_id=catalog.catalog_id,
        catalog_fingerprint=catalog.fingerprint,
    )


def admit_changes(
    payloads: Any,
    *,
    catalog: Optional[CapabilityCatalog] = None,
) -> ChangeAdmission:
    """Read a batch of payloads. Every one comes back accepted OR refused.

    The conservation property — ``len(accepted) + len(refusals) == len(payloads)``
    — is the countable form of "nothing is silently dropped", and it is what
    makes "refused whole and **recorded**" checkable at this layer, before
    ``t5``'s ledger exists. Never raises; a non-sequence offer is an empty
    result rather than an exception into a host's main path.
    """
    if not isinstance(payloads, (list, tuple)):
        return ChangeAdmission()
    accepted: list[ConfigChange] = []
    refusals: list[ConfigRefusal] = []
    for payload in payloads:
        change, refusal = change_from_payload(payload, catalog=catalog)
        if change is not None:
            accepted.append(change)
        if refusal is not None:
            refusals.append(refusal)
    return ChangeAdmission(accepted=tuple(accepted), refusals=tuple(refusals))


def revalidate(
    change: Optional[ConfigChange],
    catalog: Optional[CapabilityCatalog],
) -> Optional[ConfigRefusal]:
    """Re-check an already-accepted unit against the catalog now in force.

    The runtime half of plan risk ``r2``: a capability id is only meaningful
    relative to the catalog that declared it, so a unit validated at propose
    time and applied later must be re-checked against the declaration that is
    live *at apply time*. ``drone.py``'s staleness refusal is the precedent — it
    re-checks its declared assumptions before any code runs — and this is task
    ``t4``'s hook into the apply gate.

    Returns ``None`` when the unit still holds, otherwise a refusal:
    :data:`CHANGE_UNKNOWN_CAPABILITY` when a selected id is gone or re-kinded
    (named, because that is the actionable fact), :data:`CHANGE_STALE_CATALOG`
    when the declaration moved underneath it, and :data:`CHANGE_NO_CATALOG` when
    there is no declaration to check against at all.

    A non-capability unit revalidates trivially: prompts and knowledge reference
    no host capability, so nothing about them can go stale this way.
    """
    if not isinstance(change, CapabilitySelection):
        return None
    refusal = _check_capabilities(change, catalog)
    if refusal is not None:
        return refusal
    assert isinstance(catalog, CapabilityCatalog)  # nosec B101 - narrowed by the check above
    if catalog.catalog_id != change.catalog_id:
        return _refuse(
            CHANGE_STALE_CATALOG,
            f"the unit was validated against catalog {change.catalog_id or '(unnamed)'!s} "
            f"and the catalog now in force is {catalog.catalog_id or '(unnamed)'!s}; a "
            "capability id means nothing outside the catalog that declared it",
            target=change.target,
            origin=change.origin,
            change_id=change.change_id,
        )
    if catalog.fingerprint != change.catalog_fingerprint:
        return _refuse(
            CHANGE_STALE_CATALOG,
            "the host capability catalog changed between validation and apply "
            f"({change.catalog_fingerprint[:12]} -> {catalog.fingerprint[:12]}); the "
            "unit was refused rather than applied against a surface it was not "
            "authored for",
            target=change.target,
            origin=change.origin,
            change_id=change.change_id,
        )
    return None
