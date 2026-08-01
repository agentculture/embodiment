"""Drones — named units that mix code with *scoped* worker intelligence.

A **drone** does small-smart tasks (explore, review, search) as deterministic
logic for structure, traversal and bookkeeping, plus **scoped** calls to a
worker where judgement is genuinely needed. It is the worker-harness caste
(issue #44) made into something an operator uses (issue #45).

Why a drone rather than a subagent
----------------------------------
A subagent re-derives its approach on every invocation: no drift, full cost
every time. A drone pays the authoring cost **once** and then runs on code plus
tens of tokens: fast, repeatable, and carrying staleness risk. Authoring costs
one cortex turn — measured on this rig at 5,000–14,265 completion tokens and
400–730 s — so a task done **once** is pure loss, **2–3 times** is roughly
break-even, and a task done **often on a stable surface** wins by a widening
margin. *A drone is worth creating when the task recurs and the surface is
stable.* The failure mode is quiet waste, not a crash, which is why the
economics are stated here rather than left to be discovered.

The artifact
------------
A drone is a **committed, legible** directory — model-written code that will run
on someone else's checkout gets the same review a script would::

    .drones/<name>/
      manifest.json    name, one-line description, purpose, author model/date/
                       commit, the assumed surface, and every question it asks
                       the worker with that question's answer schema
      drone.py         the code the cortex wrote (entry point: `run(request)`)
      README.md        what it does, when it is wrong, how to re-author it

**Well-shaped is not runnable.** :func:`create` stages the three artifacts in a
temporary directory, runs a smoke invocation *against the staged copy*, and only
then moves it into place. A drone that raises, that has no ``run``, that returns
neither an answer nor a refusal, or that asks an undeclared question is never
saved. (The K1 lesson: a prior task shipped problems with ``statement=""`` and
``grade=lambda _raw: {}`` that reviewed fine for a whole task while the rung
could not be dialled. A saved-but-broken drone is that failure with a friendlier
name.)

Threat model — stated, per constraint C2, never implied by a name
-----------------------------------------------------------------
:func:`invoke` **imports and runs model-written Python in the calling process**,
under exactly the permissions that process already has. There is **no sandbox**.
The manifest's ``capabilities`` list is a *declaration for review*, not an
enforcement boundary — nothing here restricts what ``drone.py`` may do. Read
``drone.py`` before evoking a drone you did not author. This is the operator's
recorded decision for v1 (question q5): in-process under the host's existing
approval policy, with every capability declared so review is possible. The
network-less workspace jail (:mod:`embodiment.workspace`) stays *available* for
a host that wants to run a drone under it; it is not the default.

The four safeguards (task t12)
------------------------------
The design this module implements is **unvalidated** — issue #44's experiment
has not run. So the guards below are not decoration; each one is a named
failure mode from issue #45, and each is proven able to fail in
``tests/test_drone_safeguards.py``.

1. **Opt-in, and off** (claim c25). :func:`invoke` refuses to run unless
   something explicitly turned drones on: a host passing ``opt_in=``, or
   ``$EMBODIMENT_DRONES_ENABLED``. A fresh checkout evokes **nothing**. This
   is the standing rule's mirror image — a *measured* failure mode never ships
   as default behaviour, and an **unmeasured** one does not either.
2. **Staleness refusal.** Code written against a codebase encodes assumptions
   that expire, and the defect is not a crash: the drone keeps passing,
   authoritatively, on a check that no longer means anything.
   :func:`check_surface` re-checks the manifest's ``assumed_surface``;
   :func:`catalog` renders the verdict so a stale drone is visible **without
   being executed** (you see it while *choosing* a drone), and :func:`invoke`
   **refuses to run** a drone whose assumptions no longer hold rather than
   letting it report. An assumption this build cannot re-check is
   :data:`STATUS_UNVERIFIABLE` — never ``ok``.
3. **The audit trail** (claim c45). Every evocation — answers, refusals,
   "I cannot" and harness failures alike — produces an :class:`Evocation`
   carrying the drone name, the **sha256 of the bytes that ran**, the declared
   capability set and per-call acceptance, and :func:`invoke` appends it to a
   JSONL ledger. There is no code path through :func:`invoke` that runs a
   drone and leaves no record. It runs model-written code in this process
   (see the threat model above), so the record *is* the containment story.
4. **No escalation** (recorded decision c46). A drone that hits a case it
   cannot decide returns **"I cannot"** (:attr:`DroneAnswer.cannot`). There is
   no escalate-to-cortex path in v1 — that is the whole point of the cost
   model, and it is what keeps the success signal exact (*a drone's second
   evocation makes zero cortex calls*) with no exception clause. Structurally:
   a drone's only outward seam is :attr:`DroneRequest.ask`, bounded by its
   declared questions. Escalation stays an open question for #44 to answer.

What the audit trail is *not*
-----------------------------
It is traceability, not tamper-proofing. The manifest records the source hash
at authoring time and :func:`invoke` reports whether the bytes it ran still
match (:attr:`Evocation.source_matches_manifest`) — but anyone who can edit
``drone.py`` can edit ``manifest.json`` beside it. Git history and review are
the integrity boundary; these records tell you *what ran*, so a stale or
misbehaving drone is traceable from its records alone.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import re
import shutil
import subprocess  # nosec B404 - fixed argv, no shell; reads the authoring commit
import sys
import tempfile
import traceback
import types
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Optional, Sequence, Union

__all__ = [
    "AskFn",
    "DRONES_DIRNAME",
    "DRONES_ENABLED_BY_DEFAULT",
    "DRONES_ENABLED_ENV",
    "DRONE_ENTRYPOINT",
    "DRONE_LEDGER_ENV",
    "DRONE_STATUSES",
    "Drone",
    "DroneAnswer",
    "DroneCall",
    "DroneError",
    "DroneOptIn",
    "DroneRecord",
    "DroneRequest",
    "EVOCATIONS_FILENAME",
    "EVOCATION_OUTCOMES",
    "Evocation",
    "MANIFEST_SCHEMA_VERSION",
    "OPT_IN_OFF",
    "OPT_IN_SOURCES",
    "OUTCOME_ANSWERED",
    "OUTCOME_CANNOT",
    "OUTCOME_FAILED",
    "OUTCOME_REFUSED_OPT_IN",
    "OUTCOME_REFUSED_STALE",
    "SURFACE_KINDS",
    "SmokeResult",
    "STATUS_BROKEN",
    "STATUS_OK",
    "STATUS_STALE",
    "STATUS_UNCHECKED",
    "STATUS_UNVERIFIABLE",
    "StatusFn",
    "SurfaceCheck",
    "SurfaceReport",
    "UndeclaredQuestion",
    "append_evocation",
    "catalog",
    "check_surface",
    "create",
    "default_ledger",
    "find_drones_dir",
    "git_commit",
    "humanize_age",
    "invoke",
    "load",
    "mapping_ask",
    "no_worker_ask",
    "opt_in_from_env",
    "read_ledger",
    "render_catalog",
    "render_readme",
    "resolve_opt_in",
    "smoke",
    "source_hash",
    "surface_status_fn",
    "validate_answer",
    "validate_manifest",
]

# ── the artifact's fixed names ──────────────────────────────────────────────

#: Bumped when a manifest field becomes required or changes meaning. Read by
#: :func:`validate_manifest`, so an older drone fails loudly rather than being
#: silently misread.
MANIFEST_SCHEMA_VERSION = 1

DRONES_DIRNAME = ".drones"
MANIFEST_FILENAME = "manifest.json"
SOURCE_FILENAME = "drone.py"
README_FILENAME = "README.md"

#: The append-only JSONL audit trail, written beside the drones it records.
#: Dot-prefixed so :func:`catalog` (which walks directories) never sees it.
EVOCATIONS_FILENAME = ".evocations.jsonl"

#: The module-level callable every ``drone.py`` must define:
#: ``run(request: DroneRequest) -> DroneAnswer``.
DRONE_ENTRYPOINT = "run"

#: Environment override for where drones live. ``--drones-dir`` beats it.
DRONES_DIR_ENV = "EMBODIMENT_DRONES_DIR"

#: Environment override for where the evocation ledger is appended. An explicit
#: ``ledger=`` argument beats it; with neither, records land in
#: ``<drones dir>/.evocations.jsonl``.
DRONE_LEDGER_ENV = "EMBODIMENT_DRONE_LEDGER"

# ── the opt-in switch (claim c25) ───────────────────────────────────────────

#: The single environment variable that turns drone execution on.
DRONES_ENABLED_ENV = "EMBODIMENT_DRONES_ENABLED"

#: **False, and this is the feature.** The drone design is unvalidated (#44's
#: experiment has not run), and this repo's standing rule — *the measured
#: failure mode never ships as default behaviour* — has a mirror image: an
#: **unmeasured** one does not either. A fresh checkout with nothing set
#: evokes nothing. A governance guard asserts this constant is ``False``
#: without running a drone; flipping it is a deliberate, reviewable act that
#: needs #44's verdict behind it, exactly like
#: ``tests/test_zero_deps.py``'s pinned dependency set.
DRONES_ENABLED_BY_DEFAULT = False

#: Where an opt-in decision came from — recorded on every evocation so the
#: audit trail answers *who authorised this run*, not only *what ran*.
#: ``authoring`` is :func:`create`'s smoke invocation: the one named,
#: non-ambient way execution is enabled, and only for a drone being staged.
OPT_IN_SOURCES = ("default", "env", "explicit", "authoring")

#: Values that read as "on". Anything else — including an unrecognised value
#: like ``maybe`` — fails **closed**, with the reason recorded rather than
#: guessed at. An opt-in guard that resolves ambiguity in favour of running is
#: not a guard.
_TRUTHY = frozenset({"1", "true", "yes", "on", "enable", "enabled"})

# ── statuses ────────────────────────────────────────────────────────────────

#: Every declared assumption was re-checked and holds. Only ever the result of
#: a check that actually ran against a real root.
STATUS_OK = "ok"

#: At least one declared assumption was re-checked and **failed**. The drone
#: is out of date with the surface it was written against, so :func:`invoke`
#: refuses to run it: a stale drone does not crash, it reports confidently on
#: a check that no longer means anything.
STATUS_STALE = "stale"

#: Checks ran, none failed, and at least one could not be verified — an
#: assumption whose ``kind`` this build cannot re-check, or a drone that
#: declares no surface at all and therefore cannot detect its own
#: obsolescence. Deliberately not ``ok``, and deliberately not ``stale``:
#: nothing was found wrong, and nothing was confirmed right either.
STATUS_UNVERIFIABLE = "unverifiable"

#: No assumed-surface check ran. The default, and deliberately not ``"ok"``:
#: reporting a drone healthy because nobody looked is exactly the confident
#: false claim this repo's degradation rule (C3) exists to prevent.
STATUS_UNCHECKED = "unchecked"

#: The directory exists but its manifest could not be read or does not
#: validate. Rendered as a row rather than swallowed, so a broken drone is
#: visible at the moment you are choosing one.
STATUS_BROKEN = "broken"

#: The whole vocabulary ``list``'s status column may render.
DRONE_STATUSES = (
    STATUS_OK,
    STATUS_STALE,
    STATUS_UNVERIFIABLE,
    STATUS_UNCHECKED,
    STATUS_BROKEN,
)

# ── evocation outcomes ──────────────────────────────────────────────────────
#
# Distinguishable by field value alone, never by inference over a message —
# the #37 lesson (``ModelResponse`` carries no ``finish_reason``, so a
# truncated turn and a deliberate one arrive as the same object) applied
# forward. A refusal to run and a run that failed must never be one number.

#: The drone answered.
OUTCOME_ANSWERED = "answered"

#: The drone ran and returned "I cannot" — working correctly, not failing (c46).
OUTCOME_CANNOT = "cannot"

#: The harness or the drone's own code failed: it raised, did not compile, had
#: no entry point, or returned nothing usable.
OUTCOME_FAILED = "failed"

#: Nothing ran: drones are off and nothing turned them on (c25).
OUTCOME_REFUSED_OPT_IN = "refused-opt-in"

#: Nothing ran: the drone's declared assumptions no longer hold.
OUTCOME_REFUSED_STALE = "refused-stale"

EVOCATION_OUTCOMES = (
    OUTCOME_ANSWERED,
    OUTCOME_CANNOT,
    OUTCOME_FAILED,
    OUTCOME_REFUSED_OPT_IN,
    OUTCOME_REFUSED_STALE,
)

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,39}$")
_QUESTION_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_MAX_DESCRIPTION = 100
_MODULE_COUNTER = itertools.count()

_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "boolean": (bool,),
    "integer": (int,),
    "number": (int, float),
    "array": (list,),
    "object": (dict,),
    "null": (type(None),),
}


# ── errors ──────────────────────────────────────────────────────────────────


class DroneError(Exception):
    """A drone operation failed, with a remediation an agent can act on.

    Deliberately *not* a ``CliError``: this module is library-first and must
    stay importable by a host that has no CLI. The ``drone`` command group
    translates one into the other so the repo's error contract (structured
    ``error:`` / ``hint:``, never a traceback) is honoured at the boundary.
    """

    def __init__(self, message: str, remediation: str = "", *, env: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.remediation = remediation
        #: ``True`` maps to exit code 2 (environment), ``False`` to 1 (user).
        self.env = env


class UndeclaredQuestion(DroneError):
    """The drone asked the worker something its manifest never declared.

    The efficiency claim rests on the worker being asked *narrow typed
    questions*. A drone that can ask anything is a slow subagent with extra
    steps and a schema nobody validated (issue #45, failure mode 2), so an
    undeclared question fails the run rather than being answered.
    """


# ── shapes ──────────────────────────────────────────────────────────────────

#: ``ask(question_id, payload) -> answer``. Returns ``None`` when the answer is
#: unavailable or was refused for shape — never raises for that case, so a
#: drone can degrade to "I cannot" instead of crashing.
AskFn = Callable[[str, Mapping[str, Any]], Any]


@dataclass(frozen=True)
class DroneOptIn:
    """The resolved answer to *is drone execution turned on, and who turned it on*.

    Carried on every :class:`Evocation` so a record answers "who authorised
    this run" as well as "what ran". Frozen and cheap to construct, so a host
    can build one inline: ``DroneOptIn(True, "explicit", "wired by MyApp")``.
    """

    enabled: bool
    source: str = "default"
    detail: str = ""


#: The shipped default: off, because nobody said otherwise (c25).
OPT_IN_OFF = DroneOptIn(
    enabled=False,
    source="default",
    detail=(
        "drones are off by default; nothing set "
        f"${DRONES_ENABLED_ENV} and no host passed opt_in="
    ),
)

#: :func:`create`'s smoke invocation. The ONE named bypass of the ambient
#: switch, and it is not a hole: it runs only against the copy being staged in
#: a temporary directory, from source the caller handed in this second — never
#: against a saved drone. ``create`` proving that its own candidate runs is not
#: the default-on execution path c25 forbids. A structural test pins that this
#: is the only ``enabled=True`` literal in the module.
_AUTHORING_OPT_IN = DroneOptIn(
    enabled=True,
    source="authoring",
    detail="create's smoke invocation against the staged copy",
)


@dataclass(frozen=True)
class SurfaceCheck:
    """One re-checked assumption from the manifest's ``assumed_surface``.

    ``held`` is a **tri-state** on purpose: ``True`` verified, ``False``
    refuted, ``None`` *could not be checked*. Collapsing the third into either
    of the first two is the confident false claim this whole module argues
    against — an unverifiable assumption is not a passing one.
    """

    kind: str
    value: str
    held: Optional[bool]
    reason: str = ""


@dataclass(frozen=True)
class SurfaceReport:
    """The verdict on a drone's assumed surface, and the checks behind it."""

    status: str
    checks: tuple[SurfaceCheck, ...] = ()

    @property
    def stale(self) -> bool:
        return self.status == STATUS_STALE

    @property
    def failed(self) -> tuple[SurfaceCheck, ...]:
        return tuple(check for check in self.checks if check.held is False)

    @property
    def unverified(self) -> tuple[SurfaceCheck, ...]:
        return tuple(check for check in self.checks if check.held is None)

    @property
    def summary(self) -> str:
        """One line naming what actually failed — or what could not be checked."""
        if self.failed:
            detail = "; ".join(f"{c.kind} {c.value!r}: {c.reason}" for c in self.failed)
            return f"assumed surface no longer holds — {detail}"
        if self.unverified:
            detail = "; ".join(f"{c.kind} {c.value!r}: {c.reason}" for c in self.unverified)
            return f"assumed surface could not be fully re-checked — {detail}"
        if not self.checks:
            return "no assumed surface declared, so staleness cannot be checked"
        return f"assumed surface re-checked: {len(self.checks)} assumption(s) hold"

    def to_json(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "summary": self.summary,
            "checks": [
                {"kind": c.kind, "value": c.value, "held": c.held, "reason": c.reason}
                for c in self.checks
            ],
        }


@dataclass(frozen=True)
class DroneCall:
    """One scoped worker call and whether its answer was accepted.

    Interface failure and task failure must never share a number (#33 measured
    17 of 23 worker calls refused on a shape error), so acceptance is recorded
    per call and reported as its own axis.
    """

    question: str
    accepted: bool
    reason: str = ""


@dataclass(frozen=True)
class DroneAnswer:
    """What a drone returns. ``cannot`` is the v1 refusal — never an escalation."""

    answer: Optional[str] = None
    cannot: Optional[str] = None
    detail: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DroneRequest:
    """What ``run(request)`` receives."""

    name: str
    args: Mapping[str, str]
    root: Path
    home: Path
    ask: AskFn


@dataclass(frozen=True)
class Drone:
    """A loaded drone: its home directory and its validated manifest."""

    name: str
    home: Path
    manifest: Mapping[str, Any]

    @property
    def source(self) -> Path:
        return self.home / SOURCE_FILENAME

    @property
    def description(self) -> str:
        return str(self.manifest.get("description", ""))

    @property
    def capabilities(self) -> tuple[str, ...]:
        return tuple(self.manifest.get("capabilities", ()))

    @property
    def questions(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(self.manifest.get("questions", ()))

    @property
    def declared_hash(self) -> str:
        """The ``drone.py`` sha256 the manifest recorded at authoring time.

        ``""`` when the manifest declares none — a hand-rolled manifest, or one
        written before this field existed. Absent is reported as *unknown*, and
        never as a match.
        """
        return str(self.manifest.get("source_sha256", ""))


@dataclass(frozen=True)
class Evocation:
    """The audit record of one run — produced whether it answered, refused or failed.

    This is claim c45's artifact. Four things are on it for every outcome, so a
    compromised, stale or misbehaving drone is traceable from its records
    alone: the drone :attr:`name`, the :attr:`source_sha256` **of the bytes
    that ran**, the declared :attr:`capabilities`, and :attr:`call_acceptance`.
    :func:`invoke` appends one of these to a ledger on every single path.

    ``ok`` is a **derived property**, not a field: it and :attr:`outcome` are
    one fact and cannot disagree. A clean "I cannot" is ``ok`` — refusing is
    working correctly, not failing (c46).
    """

    name: str
    source_sha256: str
    capabilities: tuple[str, ...]
    calls: tuple[DroneCall, ...]
    outcome: str
    answer: Optional[str] = None
    cannot: Optional[str] = None
    detail: Mapping[str, Any] = field(default_factory=dict)
    failure: str = ""
    #: Was model-written code executed in this process? ``True`` from the
    #: moment the drone's module body is exec'd — so an import that raised
    #: still reports ``True``, because it still ran. Both refusals report
    #: ``False``: they are the paths on which nothing executed at all.
    ran: bool = False
    #: Who authorised this run.
    opt_in: DroneOptIn = OPT_IN_OFF
    #: The assumed-surface verdict, or ``None`` when the check was skipped.
    surface: Optional[SurfaceReport] = None
    #: Do the bytes that ran still match the hash the manifest recorded at
    #: authoring time? ``None`` when the manifest declares no hash — unknown,
    #: never "yes". Traceability, not tamper-proofing: see the module
    #: docstring.
    source_matches_manifest: Optional[bool] = None
    recorded_at: str = ""
    #: Where the record was appended, and whether that append succeeded. A
    #: ledger that could not be written is a degradation the host must see
    #: (C3), not an exception out of a never-raise call.
    ledger_path: str = ""
    recorded: bool = False
    record_error: str = ""

    @property
    def ok(self) -> bool:
        """The drone ran to a usable conclusion — an answer or an honest refusal."""
        return self.outcome in (OUTCOME_ANSWERED, OUTCOME_CANNOT)

    @property
    def refused(self) -> bool:
        """A safeguard stopped this run before any drone code executed."""
        return self.outcome in (OUTCOME_REFUSED_OPT_IN, OUTCOME_REFUSED_STALE)

    @property
    def calls_accepted(self) -> int:
        return sum(1 for call in self.calls if call.accepted)

    @property
    def call_acceptance(self) -> Optional[float]:
        """Accepted / asked, or ``None`` when nothing was asked.

        ``None`` rather than ``1.0``: a drone that asked nothing has no
        acceptance rate, and reporting a perfect one would be the same
        confident-about-nothing claim the whole module argues against.

        Its own axis, never folded into the outcome: #33 measured 17 of 23
        worker calls refused on a *shape* error, and a drone whose calls are
        mostly refused runs to completion and reports confidently on nothing.
        Interface failure and task failure must never share a number.
        """
        if not self.calls:
            return None
        return self.calls_accepted / len(self.calls)

    def to_json(self) -> dict[str, Any]:
        """The ledger line — plain JSON-serialisable types only."""
        return {
            "name": self.name,
            "outcome": self.outcome,
            "ok": self.ok,
            "ran": self.ran,
            "recorded_at": self.recorded_at,
            "source_sha256": self.source_sha256,
            "source_matches_manifest": self.source_matches_manifest,
            "capabilities": list(self.capabilities),
            "calls": [
                {"question": c.question, "accepted": c.accepted, "reason": c.reason}
                for c in self.calls
            ],
            "calls_asked": len(self.calls),
            "calls_accepted": self.calls_accepted,
            "call_acceptance": self.call_acceptance,
            "answer": self.answer,
            "cannot": self.cannot,
            "detail": dict(self.detail),
            "failure": self.failure,
            "opt_in": {
                "enabled": self.opt_in.enabled,
                "source": self.opt_in.source,
                "detail": self.opt_in.detail,
            },
            "surface": self.surface.to_json() if self.surface is not None else None,
        }


@dataclass(frozen=True)
class SmokeResult:
    """The proof-of-runnability that gates :func:`create`."""

    passed: bool
    summary: str
    evocation: Optional[Evocation] = None


@dataclass(frozen=True)
class DroneRecord:
    """One row of :func:`catalog` — what ``list`` renders."""

    name: str
    description: str
    authored: str
    age: str
    status: str
    model: str = ""
    commit: str = ""
    problem: str = ""


#: ``status_fn(drone) -> status``. Returning a :class:`SurfaceReport` instead of
#: a bare status string also carries the *reason* into the rendered row, which
#: is what :func:`surface_status_fn` does — a status column that says ``stale``
#: without saying which assumption broke sends you reading the drone's code.
StatusFn = Callable[[Drone], Union[str, SurfaceReport]]


# ── safeguard 1: the opt-in switch (claim c25) ──────────────────────────────


def opt_in_from_env(env: Optional[Mapping[str, str]] = None) -> DroneOptIn:
    """Read the opt-in switch out of *env*. Never raises on a malformed value.

    Follows this package's ``*_from_env`` convention (see
    :func:`embodiment.presence.cadence_from_env`): the mapping is an argument,
    so a test asserts on what it supplies rather than on the developer's shell.
    Pass ``{}`` for "a fresh checkout" — the answer is off.

    An unrecognised value fails **closed** and says so. ``DRONES=maybe`` must
    not resolve to "run model-written code in this process".
    """
    if env is None:
        env = os.environ
    raw = env.get(DRONES_ENABLED_ENV)
    if raw is None:
        return OPT_IN_OFF
    value = raw.strip().lower()
    if value in _TRUTHY:
        return DroneOptIn(
            enabled=True,
            source="env",
            detail=f"${DRONES_ENABLED_ENV}={raw!r}",
        )
    return DroneOptIn(
        enabled=False,
        source="env",
        detail=(
            f"${DRONES_ENABLED_ENV}={raw!r} does not read as on "
            f"(use one of {', '.join(sorted(_TRUTHY))}); refusing rather than guessing"
        ),
    )


def resolve_opt_in(
    opt_in: Optional[DroneOptIn] = None,
    *,
    env: Optional[Mapping[str, str]] = None,
) -> DroneOptIn:
    """An explicit host decision beats the environment, which beats the default off.

    The whole ladder ends at :data:`DRONES_ENABLED_BY_DEFAULT`, which is
    ``False``. There is no fourth rung and no config file that can quietly
    become one: a hermetic test can assert this function returns disabled for
    an empty environment **without running a drone**.
    """
    if opt_in is not None:
        return opt_in
    resolved = opt_in_from_env(env)
    if not DRONES_ENABLED_BY_DEFAULT:
        return resolved
    # Unreachable while the constant is False, and deliberately written out:
    # flipping the default is a governance act, and this is what it would do.
    if resolved.source == "default":  # pragma: no cover - the constant is False
        return DroneOptIn(True, "default", "DRONES_ENABLED_BY_DEFAULT is True")
    return resolved  # pragma: no cover - the constant is False


# ── safeguard 2: the assumed-surface re-check ───────────────────────────────

#: The assumption kinds this build can actually re-check. Anything else is
#: reported :data:`STATUS_UNVERIFIABLE`, never ``ok`` — an assumption nobody
#: knows how to check has not been checked.
SURFACE_KINDS = ("path", "glob", "contains")


def _resolve_under(root: Path, value: str) -> Optional[Path]:
    """*value* as a path under *root*, or ``None`` if it escapes the root.

    Manifests are model-written, so an ``assumed_surface`` entry of ``/etc`` or
    ``../../secrets`` is a thing that can happen. A staleness check that walks
    out of the repo is answering a different question than the one asked.
    """
    candidate = (root / value).resolve()
    root = root.resolve()
    if candidate == root or root in candidate.parents:
        return candidate
    return None


def _glob_stays_under(pattern: str) -> bool:
    """Whether *pattern* can only match inside the root it is globbed from.

    A glob pattern cannot be ``resolve()``-d the way a plain path can — the
    wildcards have no filesystem meaning until they are walked — so containment
    is decided structurally instead: an absolute pattern, or one carrying a
    ``..`` component, is refused before anything is walked.

    Deliberately conservative. A pattern like ``docs/../docs/*.md`` stays inside
    the root and is still refused, because the cost of that false negative is a
    manifest entry reported ``unverified`` (which is what this module already
    does for any assumption it cannot check), while the cost of a false positive
    is a surface check reading outside the repo.
    """
    if PurePosixPath(pattern).is_absolute() or Path(pattern).is_absolute():
        return False
    return ".." not in PurePosixPath(pattern).parts


def _check_glob(value: str, root: Path) -> SurfaceCheck:
    """The ``glob`` surface kind, contained.

    Its own function because it is the branch that got this wrong. The other two
    kinds route through :func:`_resolve_under`; this one used to skip it and
    call ``root.glob()`` directly, so ``../../*.pem`` walked out of the repo
    exactly the way ``_resolve_under``'s docstring says a model-written manifest
    will try to.

    Containment is applied **twice** on purpose: the pattern is rejected before
    it is walked (so an escaping pattern never costs a traversal), and every
    match is re-checked after (so a symlink inside the root cannot launder an
    outside path back in).
    """
    if not value:
        # Its own reason rather than the containment one: an empty pattern is
        # not an escape attempt, and saying so would be a false explanation.
        # Phrased like the text-less `contains` case, because it is the same
        # situation — the entry declares nothing to check. It used to arrive as
        # a ValueError out of `root.glob`, reporting the accident, not the cause.
        return SurfaceCheck("glob", value, None, "declares no glob pattern, so nothing was checked")
    if not _glob_stays_under(value):
        return SurfaceCheck(
            "glob", value, None, "resolves outside the root, so this check was not run"
        )
    try:
        match = next(
            (found for found in root.glob(value) if _resolve_under(root, str(found)) is not None),
            None,
        )
    # Broad by intent, and load-bearing rather than defensive habit. This ran
    # `(OSError, ValueError)`, and `Path.glob` raises **NotImplementedError** on
    # an absolute pattern — which escaped past `check_surface`'s never-raise
    # promise, out through `invoke`, which then produced NO ledger record at
    # all. That is `c45` ("every path produces exactly one Evocation and appends
    # it") broken by a manifest string. The structural guard above rejects
    # absolute patterns before they get here, so this is the second line of
    # defence: whatever a future glob implementation raises becomes a reported
    # unverified check, never an escaped exception.
    except Exception as exc:  # noqa: BLE001
        return SurfaceCheck("glob", value, None, f"glob could not be evaluated: {exc}")
    if match is None:
        return SurfaceCheck("glob", value, False, "no file matches this glob any more")
    return SurfaceCheck("glob", value, True, "")


def _check_entry(entry: Mapping[str, Any], root: Path) -> SurfaceCheck:
    kind = str(entry.get("kind", ""))
    value = str(entry.get("value", ""))
    if kind not in SURFACE_KINDS:
        return SurfaceCheck(
            kind=kind,
            value=value,
            held=None,
            reason=(
                f"kind {kind!r} is not one this build can re-check "
                f"({', '.join(SURFACE_KINDS)}) — reported as unverified, not as passing"
            ),
        )
    if kind == "glob":
        return _check_glob(value, root)

    target = _resolve_under(root, value)
    if target is None:
        return SurfaceCheck(
            kind,
            value,
            None,
            "resolves outside the root, so this check was not run",
        )
    if kind == "path":
        if target.exists():
            return SurfaceCheck(kind, value, True, "")
        return SurfaceCheck(kind, value, False, "no longer exists")

    # kind == "contains": the check that catches a *convention* moving, which
    # a bare exists-check never would — the actual staleness story in #45.
    text = entry.get("text")
    if not isinstance(text, str) or not text:
        return SurfaceCheck(
            kind, value, None, "declares no non-empty 'text' to look for, so nothing was checked"
        )
    try:
        body = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return SurfaceCheck(kind, value, False, f"cannot be read any more: {exc.strerror or exc}")
    if text in body:
        return SurfaceCheck(kind, value, True, "")
    return SurfaceCheck(kind, value, False, f"no longer contains {text!r}")


def check_surface(drone: Drone, *, root: Path) -> SurfaceReport:
    """Re-check every assumption the drone's manifest declared.

    The failure this exists for is not a crash. A ``review`` drone authored
    today may check a convention that changes next month, and it will **keep
    passing, authoritatively, on a check that no longer means anything** —
    exactly the defect class ``corrections.md`` §3 records (tests that passed
    while the thing they tested was broken). A drone that cannot detect its own
    obsolescence should not be shipped.

    One failed assumption makes the whole drone :data:`STATUS_STALE`; a drone
    is not partly trustworthy. Never raises: a check that blows up is reported
    as unverified, because a staleness check that can take down ``list`` makes
    the stale drone *less* visible, not more.
    """
    entries = drone.manifest.get("assumed_surface") or ()
    checks = tuple(_check_entry(entry, root) for entry in entries if isinstance(entry, Mapping))
    if any(check.held is False for check in checks):
        return SurfaceReport(STATUS_STALE, checks)
    if not checks or any(check.held is None for check in checks):
        return SurfaceReport(STATUS_UNVERIFIABLE, checks)
    return SurfaceReport(STATUS_OK, checks)


def surface_status_fn(root: Path) -> StatusFn:
    """The :data:`StatusFn` ``list`` wires, bound to the root to check against.

    This is what makes a stale drone visible **without being executed** — you
    see it while *choosing* a drone, not after it has run and reported
    confidently on a surface that moved.
    """

    def _status(drone: Drone) -> SurfaceReport:
        return check_surface(drone, root=root)

    return _status


# ── answer-schema validation (deliberately minimal, stdlib only) ────────────


def validate_answer(value: Any, schema: Mapping[str, Any]) -> str:
    """Return ``""`` when *value* conforms to *schema*, else a one-line reason.

    A very small subset of JSON Schema — ``type``, ``enum``, ``items``,
    ``properties``, ``required`` — chosen so the dependency gate
    (``tests/test_zero_deps.py``) stays untouched. It is enough to enforce what
    the manifest is actually for: that a declared question has an *enumerable
    or typed* answer space rather than a free-text hole.
    """
    if not isinstance(schema, Mapping):
        return "schema is not an object"
    declared = schema.get("type")
    # Four independent checks, each returning the first reason it finds. They
    # are separate functions rather than one body because they are separate
    # questions — and because the recursion in the last two reads much better
    # when the frame it re-enters is small.
    for reason in (
        _answer_type_reason(value, declared),
        _answer_enum_reason(value, schema),
        _answer_items_reason(value, schema, declared),
        _answer_properties_reason(value, schema, declared),
    ):
        if reason:
            return reason
    return ""


def _answer_type_reason(value: Any, declared: Any) -> str:
    if declared is None:
        return ""
    expected = _JSON_TYPES.get(declared)
    if expected is None:
        return f"declared type {declared!r} is not one of {sorted(_JSON_TYPES)}"
    # bool is a subclass of int in Python; JSON does not agree.
    if declared in ("integer", "number") and isinstance(value, bool):
        return f"expected {declared}, got boolean"
    if not isinstance(value, expected):
        return f"expected {declared}, got {type(value).__name__}"
    return ""


def _answer_enum_reason(value: Any, schema: Mapping[str, Any]) -> str:
    if "enum" not in schema:
        return ""
    allowed = schema["enum"]
    if not isinstance(allowed, list):
        return "enum must be a list"
    if value not in allowed:
        return f"{value!r} is not one of the declared enum values"
    return ""


def _answer_items_reason(value: Any, schema: Mapping[str, Any], declared: Any) -> str:
    if declared != "array" or not isinstance(schema.get("items"), Mapping):
        return ""
    for index, item in enumerate(value):
        reason = validate_answer(item, schema["items"])
        if reason:
            return f"item {index}: {reason}"
    return ""


def _answer_properties_reason(value: Any, schema: Mapping[str, Any], declared: Any) -> str:
    if declared != "object":
        return ""
    for key in schema.get("required") or ():
        if key not in value:
            return f"missing required key {key!r}"
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return ""
    for key, sub in properties.items():
        if key in value:
            reason = validate_answer(value[key], sub)
            if reason:
                return f"key {key!r}: {reason}"
    return ""


def _validate_schema_declaration(schema: Any) -> str:
    if not isinstance(schema, Mapping):
        return "schema must be an object"
    if "type" not in schema and "enum" not in schema:
        return "schema declares neither 'type' nor 'enum'"
    declared = schema.get("type")
    if declared is not None and declared not in _JSON_TYPES:
        return f"declared type {declared!r} is not one of {sorted(_JSON_TYPES)}"
    return ""


# ── the manifest ────────────────────────────────────────────────────────────


def _require(data: Mapping[str, Any], key: str, kind: type, where: str) -> Any:
    if key not in data:
        raise DroneError(
            f"manifest {where}is missing required field {key!r}",
            f"add {key!r} — see 'embodiment explain drone create' for the manifest shape",
        )
    value = data[key]
    if not isinstance(value, kind):
        raise DroneError(
            f"manifest field {key!r} {where}must be {kind.__name__}, got {type(value).__name__}",
            f"fix the type of {key!r} in the drone's manifest.json",
        )
    return value


def _validate_description(description: str) -> None:
    """The one-line description is required at create time, not optional metadata.

    A drone nobody can pick from ``list`` is dead weight that still cost a
    cortex turn to author, so an absent, blank or multi-line description is a
    refusal rather than a warning.
    """
    if not description.strip():
        raise DroneError(
            "a drone needs a one-line description and this one is blank",
            "pass --description 'what it does, in one line' — `list` prints it, "
            "and a drone nobody can pick is dead weight that still cost a cortex turn",
        )
    if "\n" in description or "\r" in description:
        raise DroneError(
            "the drone description must be a single line",
            "collapse it to one line; put the longer story in 'purpose'",
        )
    if len(description) > _MAX_DESCRIPTION:
        raise DroneError(
            f"the drone description is {len(description)} chars; "
            f"the limit is {_MAX_DESCRIPTION}",
            "shorten it — it has to fit a column in `embodiment drone list`",
        )


def _validate_author(author: Mapping[str, Any]) -> None:
    for key in ("model", "date", "commit"):
        value = author.get(key)
        if not isinstance(value, str) or not value.strip():
            raise DroneError(
                f"manifest author.{key} is missing or blank",
                "a drone is model-written code that will run on someone else's "
                "checkout; who wrote it, when, and against which commit is part "
                "of reading its output honestly",
            )


def _validate_questions(questions: Sequence[Any]) -> dict[str, Mapping[str, Any]]:
    by_id: dict[str, Mapping[str, Any]] = {}
    for index, question in enumerate(questions):
        if not isinstance(question, Mapping):
            raise DroneError(
                f"manifest questions[{index}] is not an object",
                "each question is {'id': ..., 'prompt': ..., 'schema': {...}}",
            )
        qid = question.get("id")
        if not isinstance(qid, str) or not _QUESTION_ID_RE.match(qid):
            raise DroneError(
                f"manifest questions[{index}] has an invalid id {qid!r}",
                "ids are lowercase [a-z0-9_-], up to 64 chars",
            )
        if qid in by_id:
            raise DroneError(
                f"manifest declares question id {qid!r} twice",
                "question ids must be unique — `ask` resolves the schema by id",
            )
        prompt = question.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise DroneError(
                f"manifest question {qid!r} has no prompt",
                "declare the question the worker is actually asked",
            )
        reason = _validate_schema_declaration(question.get("schema"))
        if reason:
            raise DroneError(
                f"manifest question {qid!r} has an unusable answer schema: {reason}",
                "a scoped call needs an enumerable or typed answer space; "
                "a free-text hole is a slow subagent with extra steps",
            )
        by_id[qid] = question
    return by_id


def _validate_smoke_block(smoke_block: Mapping[str, Any], questions: Mapping[str, Any]) -> None:
    args = smoke_block.get("args", {})
    if not isinstance(args, Mapping):
        raise DroneError(
            "manifest smoke.args must be an object",
            "smoke.args is the argument map the smoke invocation runs with",
        )
    answers = smoke_block.get("answers")
    if not isinstance(answers, Mapping):
        raise DroneError(
            "manifest smoke.answers is missing or is not an object",
            "declare one canned answer per question so the smoke run is hermetic",
        )
    missing = sorted(set(questions) - set(answers))
    if missing:
        raise DroneError(
            f"manifest smoke.answers has no canned answer for: {', '.join(missing)}",
            "every declared question needs a canned answer — it is what proves "
            "the schema is satisfiable and lets the smoke run without a worker",
        )
    unknown = sorted(set(answers) - set(questions))
    if unknown:
        raise DroneError(
            f"manifest smoke.answers answers undeclared question(s): {', '.join(unknown)}",
            "usually a typo'd question id; every answer must match a declared question",
        )
    for qid, answer in answers.items():
        reason = validate_answer(answer, questions[qid]["schema"])
        if reason:
            raise DroneError(
                f"manifest smoke answer for {qid!r} violates its own declared schema: {reason}",
                "a canned answer that does not satisfy the schema means the schema "
                "and the drone disagree before a worker is ever involved",
            )


def _validate_assumed_surface(entries: list) -> None:
    """Every declared assumption must be re-checkable, or `create` refuses it.

    Extracted from :func:`validate_manifest` rather than inlined: this is the
    staleness story's entry point, and the three refusals below are the whole
    reason a drone can detect its own obsolescence.
    """
    for entry in entries:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("value"), str):
            raise DroneError(
                "each assumed_surface entry needs a 'kind' and a string 'value'",
                "record the paths, conventions and versions the drone assumes — "
                "they are what a staleness check has to re-check",
            )
        if not isinstance(entry.get("kind"), str) or not entry["kind"].strip():
            raise DroneError(
                "each assumed_surface entry needs a non-blank 'kind'",
                "e.g. {'kind': 'path', 'value': 'embodiment/cli/_commands/'}",
            )
        if entry["kind"] == "contains" and not str(entry.get("text") or "").strip():
            raise DroneError(
                f"assumed_surface entry {entry['value']!r} is kind 'contains' "
                "but declares no non-blank 'text' to look for",
                "a 'contains' assumption names the convention it depends on: "
                "{'kind': 'contains', 'value': 'embodiment/cli/__init__.py', "
                "'text': 'def register('} — without it nothing can be re-checked",
            )


def _validate_capabilities(capabilities: list) -> None:
    for capability in capabilities:
        if not isinstance(capability, str) or not capability.strip():
            raise DroneError(
                "every declared capability must be a non-blank string",
                "capabilities are a declaration for review, not an enforcement "
                "boundary — see the threat model in the drone's README",
            )


def validate_manifest(data: Any) -> None:
    """Raise :class:`DroneError` unless *data* is a well-formed drone manifest."""
    if not isinstance(data, Mapping):
        raise DroneError("manifest.json is not a JSON object", "rewrite it as an object")
    schema_version = _require(data, "schema", int, "")
    if schema_version != MANIFEST_SCHEMA_VERSION:
        raise DroneError(
            f"manifest schema version {schema_version} is not "
            f"{MANIFEST_SCHEMA_VERSION} (this build's version)",
            "re-author the drone against the current manifest shape",
        )
    name = _require(data, "name", str, "")
    if not _NAME_RE.match(name):
        raise DroneError(
            f"manifest name {name!r} is not a valid drone name",
            "names are lowercase [a-z0-9._-], up to 40 chars, starting alphanumeric",
        )
    _validate_description(_require(data, "description", str, ""))
    purpose = _require(data, "purpose", str, "")
    if not purpose.strip():
        raise DroneError(
            "manifest purpose is blank",
            "say what the drone is for at more length than the one-line description",
        )
    _validate_author(_require(data, "author", dict, ""))
    _validate_assumed_surface(_require(data, "assumed_surface", list, ""))
    _validate_capabilities(_require(data, "capabilities", list, ""))
    questions = _validate_questions(_require(data, "questions", list, ""))
    _validate_smoke_block(_require(data, "smoke", dict, ""), questions)


# ── loading ─────────────────────────────────────────────────────────────────


def load(name: str, drones_dir: Path) -> Drone:
    """Load and validate one drone, or raise :class:`DroneError`."""
    home = drones_dir / name
    if not home.is_dir():
        raise DroneError(
            f"no drone named {name!r} in {drones_dir}",
            "see what exists with: embodiment drone list",
        )
    manifest = _read_manifest(home)
    validate_manifest(manifest)
    if not (home / SOURCE_FILENAME).is_file():
        raise DroneError(
            f"drone {name!r} has no {SOURCE_FILENAME}",
            "the manifest survived but the code did not; re-author the drone",
        )
    return Drone(name=name, home=home, manifest=manifest)


def _read_manifest(home: Path) -> Mapping[str, Any]:
    path = home / MANIFEST_FILENAME
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DroneError(
            f"cannot read {path}: {exc.strerror or exc}",
            "check the drone directory is readable",
            env=True,
        ) from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise DroneError(
            f"{path} is not valid JSON: {exc}",
            "a manifest is hand-reviewable JSON; fix or re-author the drone",
        ) from exc


def source_hash(source: Path) -> str:
    """SHA-256 of the drone source, for the evocation audit trail t12 writes.

    An independent re-read, for a caller checking a drone *without* running it.
    :func:`invoke` deliberately does **not** call this: it hashes the same
    bytes it compiles, so its recorded hash always describes the code that
    actually ran.
    """
    try:
        return hashlib.sha256(source.read_bytes()).hexdigest()
    except OSError:
        return ""


# ── the ask seams ───────────────────────────────────────────────────────────


def mapping_ask(answers: Mapping[str, Any]) -> AskFn:
    """An :data:`AskFn` that answers from a fixed map — used by smoke and ``--answers``."""

    def _ask(question_id: str, payload: Mapping[str, Any]) -> Any:
        return answers.get(question_id)

    return _ask


def no_worker_ask(question_id: str, payload: Mapping[str, Any]) -> Any:
    """The default: no worker seam is wired, so every scoped question is unanswered.

    Returns ``None`` rather than raising. A drone is expected to handle an
    unanswered question by returning "I cannot" — that is the v1 contract, and
    it is why there is no escalation path to reintroduce the cost the drone
    exists to avoid.
    """
    return None


class _RecordingAsk:
    """Wraps a host :data:`AskFn`: enforces declaration, validates, records.

    ``None`` is the single "no usable answer" signal — unavailable and refused
    collapse into it deliberately, so a drone has one case to handle rather
    than two. The cost, stated rather than discovered: a question whose
    *legitimate* answer is JSON ``null`` can never be accepted. Declare such a
    question with an enum (``{"enum": ["none", "some"]}``) instead of
    ``{"type": "null"}``.
    """

    def __init__(self, questions: Mapping[str, Mapping[str, Any]], inner: AskFn) -> None:
        self._questions = questions
        self._inner = inner
        self.calls: list[DroneCall] = []

    def __call__(self, question_id: str, payload: Optional[Mapping[str, Any]] = None) -> Any:
        if question_id not in self._questions:
            raise UndeclaredQuestion(
                f"the drone asked an undeclared question: {question_id!r}",
                "declare every worker question and its answer schema in "
                "manifest.json — an undeclared question is an unvalidated one",
            )
        answer = self._inner(question_id, payload or {})
        if answer is None:
            self.calls.append(
                DroneCall(question=question_id, accepted=False, reason="no answer available")
            )
            return None
        reason = validate_answer(answer, self._questions[question_id]["schema"])
        if reason:
            self.calls.append(DroneCall(question=question_id, accepted=False, reason=reason))
            return None
        self.calls.append(DroneCall(question=question_id, accepted=True))
        return answer


# ── safeguard 3: the audit ledger (claim c45) ───────────────────────────────


def default_ledger(drones_dir: Path, env: Optional[Mapping[str, str]] = None) -> Path:
    """Where evocation records are appended: ``$EMBODIMENT_DRONE_LEDGER``, else
    ``<drones_dir>/.evocations.jsonl``."""
    if env is None:
        env = os.environ
    override = env.get(DRONE_LEDGER_ENV)
    if override:
        return Path(override).expanduser()
    return drones_dir / EVOCATIONS_FILENAME


def append_evocation(evocation: Evocation, ledger: Path) -> tuple[bool, str]:
    """Append one record as a JSONL line. Returns ``(written, error)``.

    Never raises. A read-only filesystem is a degradation the host must be told
    about (C3) — but a harness that dies while *recording* a run that already
    happened is strictly worse than one that reports it could not record.
    """
    try:
        line = json.dumps(evocation.to_json(), ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as exc:  # pragma: no cover - to_json is plain types
        return False, f"record is not JSON-serialisable: {exc}"
    try:
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with ledger.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError as exc:
        return False, f"cannot append to {ledger}: {exc.strerror or exc}"
    return True, ""


def read_ledger(ledger: Path) -> list[dict[str, Any]]:
    """Every record in *ledger*, oldest first. Never raises.

    An unreadable ledger reads as empty and a corrupt line is skipped: this is
    for reading an audit trail back, and one bad line must not hide the rest.
    """
    try:
        text = ledger.read_text(encoding="utf-8")
    except OSError:
        return []
    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


# ── running ─────────────────────────────────────────────────────────────────


def _compile_source(source: Path, source_bytes: bytes) -> types.CodeType:
    """Compile **the exact bytes the caller already read and hashed**.

    Not re-read from the path, and deliberately NOT loaded through
    ``importlib.util.spec_from_file_location``. Two reasons, both load-bearing:

    1. **The bytecode cache can serve stale code.** ``SourceFileLoader``
       validates a cached ``.pyc`` on *(mtime, source size)*. Re-author a drone
       within the same second to a body of the same byte length — entirely
       ordinary for a one-line fix — and the loader reuses the OLD bytecode. A
       drone that silently runs its previous version is the exact
       confidently-out-of-date failure this whole feature is built to avoid,
       and it would be near-undebuggable in the field. Found by a handoff probe
       during t11, not in review.
    2. **The hash must describe the code that ran.** Reading once closes the
       gap where the bytes hashed for the audit trail and the bytes executed
       could differ.

    Compiling is separated from executing so the audit record can say whether
    model-written code actually ran: a source that does not compile never
    executed a line, and :attr:`Evocation.ran` must not claim otherwise.
    """
    try:
        return compile(source_bytes, str(source), "exec")
    except (SyntaxError, ValueError) as exc:
        raise DroneError(
            f"{SOURCE_FILENAME} does not compile: {exc.__class__.__name__}: {exc}",
            "drone.py must be a valid Python source file; re-author the drone",
        ) from exc


def _load_entrypoint(source: Path, name: str, code: types.CodeType) -> Callable[..., Any]:
    """Execute *code* in a fresh namespace and return its ``run``.

    The module is registered in ``sys.modules`` only *while* the body executes
    (dataclasses and similar machinery look the module up by name during class
    creation) and removed afterwards — otherwise ``sys.modules`` would grow by
    one entry per evocation. The returned function keeps its own globals alive
    through ``__globals__``.
    """
    module_name = f"_embodiment_drone_{re.sub(r'[^a-z0-9_]', '_', name)}_{next(_MODULE_COUNTER)}"
    module = types.ModuleType(module_name)
    module.__file__ = str(source)
    sys.modules[module_name] = module
    try:
        try:
            # Executing the drone IS the feature, and it is the whole reason
            # this module's threat model is stated rather than implied: this
            # runs model-written code in the calling process, with no sandbox.
            exec(code, module.__dict__)  # nosec B102 - see the module docstring's threat model
        # Broad by intent: this is model-written code, so the contract is to
        # report whatever it raised, never to let a traceback escape.
        except Exception as exc:  # noqa: BLE001
            raise DroneError(
                f"{SOURCE_FILENAME} failed to import: {exc.__class__.__name__}: {exc}",
                "the drone's own source raised at import time; re-author it",
            ) from exc
        entrypoint = getattr(module, DRONE_ENTRYPOINT, None)
        if not callable(entrypoint):
            raise DroneError(
                f"{SOURCE_FILENAME} defines no callable {DRONE_ENTRYPOINT}(request)",
                f"a drone's entry point is `def {DRONE_ENTRYPOINT}(request) -> DroneAnswer`",
            )
        return entrypoint
    finally:
        sys.modules.pop(module_name, None)


def _coerce_answer(result: Any) -> DroneAnswer:
    """Normalise whatever ``run`` returned into a :class:`DroneAnswer`."""
    if isinstance(result, DroneAnswer):
        return result
    if isinstance(result, str):
        return DroneAnswer(answer=result)
    if isinstance(result, Mapping):
        detail = result.get("detail") or {}
        return DroneAnswer(
            answer=result.get("answer"),
            cannot=result.get("cannot"),
            detail=detail if isinstance(detail, Mapping) else {},
        )
    raise DroneError(
        f"{DRONE_ENTRYPOINT}() returned {type(result).__name__}, "
        "not a DroneAnswer, str or mapping",
        "return DroneAnswer(answer=...) or DroneAnswer(cannot='...')",
    )


def _prepare_entrypoint(drone: Drone, state: dict[str, Any]) -> Callable[..., Any]:
    """Read, hash, compile and load the drone's ``run``. Raises :class:`DroneError`.

    Raising rather than returning a record: every caller is ``invoke``, which
    turns a ``DroneError`` into exactly one ledger entry, so the single
    record-per-path contract stays where it can be read in one place.

    ``state`` is mutated on the way through, and the order matters. ``sha`` is
    set from the bytes that were *actually read* — ONE read, so the bytes hashed
    for the audit trail are the bytes executed. ``ran`` flips before
    :func:`_load_entrypoint`, because importing the module **is** execution of
    model-written code; a record claiming otherwise would be false.
    """
    try:
        source_bytes = drone.source.read_bytes()
    except OSError as exc:
        raise DroneError(f"cannot read {drone.source}: {exc.strerror or exc}") from exc
    state["sha"] = hashlib.sha256(source_bytes).hexdigest()
    code = _compile_source(drone.source, source_bytes)
    # From here on, model-written code has executed in this process.
    state["ran"] = True
    return _load_entrypoint(drone.source, drone.name, code)


def _run_entrypoint(entrypoint: Callable[..., Any], request: "DroneRequest") -> Any:
    """Call the drone and normalise every failure into a :class:`DroneError`.

    The located message — file and line out of the traceback — is the whole
    value here: a drone that raises should tell its author *where*, and this is
    the only frame that still has the traceback to say so.
    """
    try:
        return entrypoint(request)
    except UndeclaredQuestion as exc:
        raise DroneError(exc.message) from exc
    # Broad by intent: model-written code, reported rather than raised.
    except Exception as exc:  # noqa: BLE001
        where = traceback.extract_tb(exc.__traceback__)[-1]
        raise DroneError(
            f"{DRONE_ENTRYPOINT}() raised {exc.__class__.__name__}: {exc} "
            f"(at {Path(where.filename).name}:{where.lineno})"
        ) from exc


def _refusal_before_running(
    drone: Drone,
    *,
    root: Path,
    resolved: DroneOptIn,
    stale_ok: bool,
    state: dict[str, Any],
    record: Callable[..., Evocation],
) -> Optional[Evocation]:
    """The two guards that run before a byte of model-written code executes.

    Returns the refusal record, or ``None`` to proceed. Extracted from
    :func:`invoke` so the caller reads as *guards, then run* — but it keeps
    returning a record rather than raising, because ``invoke``'s contract is
    that **every** path produces exactly one :class:`Evocation`, and a guard
    that raised would be the one path that did not.

    Both refusals hash the source first. The record names *which bytes* were
    refused, so a refusal is as traceable as a run; ``ran`` stays ``False``,
    because nothing about that hash claims the code executed.
    """
    if not resolved.enabled:
        state["sha"] = source_hash(drone.source)
        return record(
            OUTCOME_REFUSED_OPT_IN,
            failure=(
                f"{resolved.detail}. Turn them on deliberately with "
                f"{DRONES_ENABLED_ENV}=1, or pass opt_in= from the host. The design is "
                "unvalidated (issue #44's experiment has not run) and an unmeasured "
                "behaviour does not ship on by default."
            ),
        )

    if stale_ok:
        return None

    surface = check_surface(drone, root=root)
    state["surface"] = surface
    # Written positively, not as an early `if not surface.stale: return None`.
    # `tests/test_drone_safeguards.py` mutates this exact line out of the module
    # source to prove the guard is what stops a stale drone, and asserts the
    # string it rewrites occurs exactly once — so inverting it silently breaks a
    # test-of-the-test rather than a test.
    if surface.stale:
        state["sha"] = source_hash(drone.source)
        return record(
            OUTCOME_REFUSED_STALE,
            failure=(
                f"{surface.summary}. Re-author it ('embodiment drone create "
                f"{drone.name} --force'), or run it anyway with --stale-ok and read "
                "its answer knowing an assumption it depends on is false."
            ),
        )
    return None


def invoke(
    drone: Drone,
    *,
    root: Path,
    args: Optional[Mapping[str, str]] = None,
    ask: AskFn = no_worker_ask,
    opt_in: Optional[DroneOptIn] = None,
    stale_ok: bool = False,
    ledger: Optional[Path] = None,
) -> Evocation:
    """Run *drone* under the safeguards and return the record — always.

    Never raises for a failure inside the drone: a harness that raises cannot
    hand back a record of what went wrong, and C3 requires every degradation to
    be observable to the host rather than escaping as an exception.

    **Every path through this function produces exactly one
    :class:`Evocation` and appends it to the ledger** — answers, "I cannot",
    harness failures, and both safeguard refusals. There is no way to run a
    drone here and leave no record; that property is what claim c45 asks for,
    and a test asserts a run that leaves no record is a failure.

    Two guards run *before* a byte of model-written code executes:

    * ``opt_in`` — an explicit :class:`DroneOptIn`, else
      ``$EMBODIMENT_DRONES_ENABLED``, else off (c25). Refusal is
      :data:`OUTCOME_REFUSED_OPT_IN`.
    * the assumed surface — refusal is :data:`OUTCOME_REFUSED_STALE` unless
      *stale_ok*. A stale drone reports confidently on a check that no longer
      means anything, which is worse than not running.

    **When it does run, this imports and executes model-written Python in this
    process.** See the module docstring's threat model.
    """
    questions = {str(q["id"]): q for q in drone.questions}
    recorder = _RecordingAsk(questions, ask)
    capabilities = drone.capabilities
    resolved = resolve_opt_in(opt_in)
    if ledger is None:
        ledger = default_ledger(drone.home.parent)
    state: dict[str, Any] = {"sha": "", "ran": False, "surface": None}

    def _record(outcome: str, **fields: Any) -> Evocation:
        sha = str(state["sha"])
        declared = drone.declared_hash
        # One field dict, two constructions: the record written to the ledger
        # and the record handed back differ only in whether the write landed.
        # Built this way rather than `replace()`-ing the first into the second
        # because `dataclasses.replace` erases to the base dataclass protocol
        # under some type checkers, and this function's contract is specifically
        # an Evocation — the record, not any dataclass.
        common: dict[str, Any] = dict(
            name=drone.name,
            source_sha256=sha,
            capabilities=capabilities,
            calls=tuple(recorder.calls),
            outcome=outcome,
            ran=bool(state["ran"]),
            opt_in=resolved,
            surface=state["surface"],
            source_matches_manifest=(sha == declared) if (declared and sha) else None,
            recorded_at=datetime.now(timezone.utc).isoformat(),
            ledger_path=str(ledger),
            **fields,
        )
        written, error = append_evocation(Evocation(**common), ledger)
        return Evocation(**common, recorded=written, record_error=error)

    def _fail(message: str) -> Evocation:
        return _record(OUTCOME_FAILED, failure=message)

    refusal = _refusal_before_running(
        drone,
        root=root,
        resolved=resolved,
        stale_ok=stale_ok,
        state=state,
        record=_record,
    )
    if refusal is not None:
        return refusal

    try:
        entrypoint = _prepare_entrypoint(drone, state)
    except DroneError as exc:
        return _fail(exc.message)

    request = DroneRequest(
        name=drone.name,
        args=dict(args or {}),
        root=root,
        home=drone.home,
        ask=recorder,
    )
    try:
        answer = _coerce_answer(_run_entrypoint(entrypoint, request))
    except DroneError as exc:
        return _fail(exc.message)

    has_answer = bool(answer.answer and str(answer.answer).strip())
    has_refusal = bool(answer.cannot and str(answer.cannot).strip())
    if not has_answer and not has_refusal:
        return _fail(
            f"{DRONE_ENTRYPOINT}() returned neither an answer nor an 'I cannot' — "
            "a drone that reports nothing has not run, it has only been shaped"
        )

    return _record(
        OUTCOME_ANSWERED if has_answer else OUTCOME_CANNOT,
        answer=answer.answer if has_answer else None,
        cannot=answer.cannot if has_refusal else None,
        detail=dict(answer.detail),
    )


def smoke(
    drone: Drone,
    *,
    root: Path,
    opt_in: Optional[DroneOptIn] = None,
    ledger: Optional[Path] = None,
) -> SmokeResult:
    """Prove the drone actually runs, using only its manifest's canned answers.

    Hermetic by construction: every declared question carries a canned answer
    validated against its own schema, so no worker is dialled. Passing means
    the code imports, the entry point exists, the run completes, every question
    it asked was declared, and it returned an answer or a refusal.

    Under the same opt-in guard as :func:`invoke`, and for the same reason:
    this executes the drone. :func:`create` passes the authoring opt-in for the
    copy it is staging, so authoring a drone works on a checkout where drones
    are off — but calling ``smoke`` on a *saved* drone does not become a way
    around the switch.

    The assumed-surface check is skipped (``stale_ok``): authoring is where the
    surface is *declared*, and re-checking a claim against the moment it was
    made proves nothing about later. Staleness is ``list``'s and ``evoke``'s
    question.
    """
    smoke_block = drone.manifest.get("smoke", {})
    evocation = invoke(
        drone,
        root=root,
        args=smoke_block.get("args", {}),
        ask=mapping_ask(smoke_block.get("answers", {})),
        opt_in=opt_in,
        stale_ok=True,
        ledger=ledger,
    )
    if not evocation.ok:
        return SmokeResult(False, evocation.failure, evocation)
    refused = [call for call in evocation.calls if not call.accepted]
    if refused:
        detail = "; ".join(f"{call.question}: {call.reason}" for call in refused)
        return SmokeResult(
            False,
            f"{len(refused)} of {len(evocation.calls)} scoped calls were refused "
            f"against the manifest's own canned answers ({detail})",
            evocation,
        )
    outcome = "answered" if evocation.answer else "returned 'I cannot'"
    if evocation.calls:
        scope = f"after {len(evocation.calls)} scoped call(s), all accepted"
    else:
        scope = "with no scoped calls — a pure code-drone, zero worker tokens"
    return SmokeResult(True, f"{outcome} {scope}", evocation)


# ── authoring ───────────────────────────────────────────────────────────────


def _now(clock: Optional[Callable[[], datetime]]) -> datetime:
    return (clock or (lambda: datetime.now(timezone.utc)))()


def git_commit(root: Path) -> str:
    """The commit a drone was authored against, or ``""`` when unknowable.

    Never raises: provenance that cannot be read is recorded as absent, which
    is honest, rather than blocking authoring on a git checkout existing.
    """
    # Resolved rather than spelled "git": a partial executable path resolves
    # against whatever PATH the host process happens to carry.
    git = shutil.which("git")
    if git is None:
        return ""
    try:
        proc = subprocess.run(  # nosec B603 - resolved argv, shell=False, no user input
            [git, "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _build_manifest(
    name: str,
    draft: Mapping[str, Any],
    *,
    description: Optional[str],
    author_model: Optional[str],
    commit: Optional[str],
    authored_at: datetime,
) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "schema": MANIFEST_SCHEMA_VERSION,
        "name": name,
        "description": "",
        "purpose": "",
        "author": {},
        "assumed_surface": [],
        "capabilities": [],
        "questions": [],
        "smoke": {"args": {}, "answers": {}},
    }
    for key, value in draft.items():
        manifest[key] = value
    manifest["schema"] = MANIFEST_SCHEMA_VERSION
    manifest["name"] = name

    if description is not None:
        manifest["description"] = description
    _validate_description(str(manifest.get("description", "")))

    draft_author = manifest.get("author")
    author = dict(draft_author) if isinstance(draft_author, Mapping) else {}
    if author_model:
        author["model"] = author_model
    if commit is not None:
        author["commit"] = commit
    author.setdefault("commit", "")
    author["date"] = authored_at.isoformat()
    # An absent commit is recorded as an explicit "unknown" rather than an
    # empty string: a blank field reads as "not filled in yet", and this one is
    # a claim — nobody could determine the commit.
    if not str(author.get("commit", "")).strip():
        author["commit"] = "unknown"
    if not str(author.get("model", "")).strip():
        author["model"] = "unknown"
    manifest["author"] = author

    smoke_block = manifest.get("smoke")
    manifest["smoke"] = dict(smoke_block) if isinstance(smoke_block, Mapping) else {}
    manifest["smoke"].setdefault("args", {})
    manifest["smoke"].setdefault("answers", {})
    return manifest


def create(
    name: str,
    *,
    source: Path,
    drones_dir: Path,
    draft: Optional[Mapping[str, Any]] = None,
    description: Optional[str] = None,
    author_model: Optional[str] = None,
    commit: Optional[str] = None,
    notes: Optional[str] = None,
    force: bool = False,
    clock: Optional[Callable[[], datetime]] = None,
) -> Drone:
    """Author a drone — and refuse to save one that has not proven it runs.

    The three artifacts are staged in a temporary directory, the smoke
    invocation runs **against the staged copy** (so what is proven is what will
    be saved, not the source that was handed in), and only a pass moves the
    directory into ``drones_dir``. A failure leaves ``drones_dir/<name>``
    absent — an unsaved failure is cheap, a saved broken drone is a trap.
    """
    if not _NAME_RE.match(name):
        raise DroneError(
            f"{name!r} is not a valid drone name",
            "names are lowercase [a-z0-9._-], up to 40 chars, starting alphanumeric",
        )
    try:
        source_text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise DroneError(
            f"cannot read the drone source {source}: {exc.strerror or exc}",
            "pass --source pointing at the drone.py the cortex wrote",
            env=True,
        ) from exc
    if not source_text.strip():
        raise DroneError(
            f"the drone source {source} is empty",
            "a drone with no code is a subagent invocation with extra steps",
        )

    target = drones_dir / name
    if target.exists() and not force:
        raise DroneError(
            f"a drone named {name!r} already exists at {target}",
            "pass --force to replace it, or pick another name; "
            "check what exists with: embodiment drone list",
        )

    authored_at = _now(clock)
    root = drones_dir.parent
    manifest = _build_manifest(
        name,
        draft or {},
        description=description,
        author_model=author_model,
        commit=commit if commit is not None else git_commit(root),
        authored_at=authored_at,
    )
    validate_manifest(manifest)

    staging_parent = tempfile.mkdtemp(prefix="embodiment-drone-")
    try:
        staged = Path(staging_parent) / name
        staged.mkdir()
        _write_artifacts(staged, manifest, source_text, notes)
        candidate = Drone(name=name, home=staged, manifest=manifest)
        # The authoring opt-in, and the staged copy only: `create` proving its
        # own candidate runs is not the default-on execution path c25 forbids,
        # so authoring works on a checkout where drones are off. The smoke
        # record lands in the staging directory and is discarded with it; what
        # survives is the summary written into the manifest below.
        result = smoke(candidate, root=root, opt_in=_AUTHORING_OPT_IN)
        if not result.passed:
            raise DroneError(
                f"drone {name!r} failed its smoke invocation and was NOT saved: "
                f"{result.summary}",
                "fix drone.py (or its declared questions) and run create again — "
                "well-shaped is not runnable, and a saved broken drone is a trap",
            )
        manifest["smoke"] = dict(manifest["smoke"])
        manifest["smoke"]["passed"] = True
        manifest["smoke"]["at"] = authored_at.isoformat()
        manifest["smoke"]["summary"] = result.summary
        _write_artifacts(staged, manifest, source_text, notes)

        drones_dir.mkdir(parents=True, exist_ok=True)
        _install(staged, target)
    finally:
        shutil.rmtree(staging_parent, ignore_errors=True)

    return Drone(name=name, home=target, manifest=manifest)


def _install(staged: Path, target: Path) -> None:
    """Put *staged* at *target*, never destroying an existing drone first.

    The naive ``rmtree(target); move(staged, target)`` has a window with real
    cost: *staged* lives in a system temp dir, so :func:`shutil.move` is
    usually a cross-filesystem copytree and can fail partway — disk full,
    permissions — **after** the old drone is already gone. Losing a working
    drone costs a cortex turn to rebuild, which is precisely the expense this
    whole feature exists to stop paying twice.

    So the fallible copy happens **first**, into a sibling of *target*, and the
    destructive part becomes two same-filesystem renames that either both
    happen or leave the original standing.

    Found by review after `t11` merged; re-implemented against `t12`'s rewrite.
    """
    incoming = target.with_name(f".{target.name}.incoming")
    backup = target.with_name(f".{target.name}.backup")
    for leftover in (incoming, backup):
        shutil.rmtree(leftover, ignore_errors=True)
    try:
        # The fallible step, done FIRST: a cross-filesystem copytree that can
        # fail partway. `target` is still untouched if it does.
        shutil.move(str(staged), str(incoming))
        had_previous = target.exists()
        if had_previous:
            os.rename(target, backup)
        try:
            os.rename(incoming, target)
        except OSError:
            if had_previous:  # put the working drone back before reporting
                os.rename(backup, target)
            raise
    except OSError as exc:
        raise DroneError(
            f"drone {target.name!r} passed its smoke run but could not be "
            f"installed at {target}: {exc.strerror or exc}",
            "any drone already at that path was left intact; free some space "
            "or fix permissions and run create again",
            env=True,
        ) from exc
    finally:
        shutil.rmtree(incoming, ignore_errors=True)
        shutil.rmtree(backup, ignore_errors=True)


def _write_artifacts(
    home: Path, manifest: dict[str, Any], source_text: str, notes: Optional[str]
) -> None:
    """Write the three artifacts, stamping the source hash the audit trail compares against.

    The hash is taken from the file **after** it is written, never from
    ``source_text`` in memory: what a later :func:`invoke` reads back is the
    only thing worth recording, and the two can differ (text-mode newline
    translation) on a platform that is not this one.
    """
    (home / SOURCE_FILENAME).write_text(source_text, encoding="utf-8")
    manifest["source_sha256"] = source_hash(home / SOURCE_FILENAME)
    (home / MANIFEST_FILENAME).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (home / README_FILENAME).write_text(render_readme(manifest, notes), encoding="utf-8")


_THREAT_MODEL = """\
## Threat model — read this before evoking

`embodiment drone evoke` **imports and runs this model-written Python in the
calling process**, with exactly the permissions that process already has. There
is **no sandbox**. The `capabilities` list in `manifest.json` is a *declaration
for review*, not an enforcement boundary — nothing in embodiment restricts what
`drone.py` may do.

Read `drone.py` before evoking a drone you did not author, exactly as you would
any committed script. The network-less workspace jail
(`embodiment/workspace.py`) is available for a host that wants to run a drone
under it; it is not the default.

Because there is no sandbox, **the audit trail is what you get instead**:
every evocation — answers, refusals and failures alike — appends a record
naming this drone, the sha256 of the bytes that actually ran, the declared
capabilities above and the acceptance of every scoped call, to
`.drones/.evocations.jsonl`.

**It is traceability, not tamper-proofing, and the difference matters before
you evoke rather than after.** The records tell you *what ran*; they do not
stop anything from running. Anyone who can edit `drone.py` can edit
`manifest.json` beside it, so the recorded hash catches drift and accident,
never a determined edit. **Git history and review are the integrity
boundary** — read this drone the way you would read any script that is about
to execute on your machine, because that is what it is.

## Turning drones on

Drones are **opt-in and off**. `evoke` refuses to run this drone unless
`EMBODIMENT_DRONES_ENABLED=1` is set (or a host passes `opt_in=` through the
library). The design is unvalidated, and an unmeasured behaviour does not ship
on by default.
"""


def _bullets(items: list[str], *, empty: str) -> list[str]:
    """*items*, or a single line saying there are none.

    The three list sections of a generated README all had this shape written
    out longhand. "None declared" is a claim the README has to make explicitly —
    an empty section reads as an omission, and for `assumed_surface` that is
    exactly the difference between *assumes nothing* and *forgot to say*.
    """
    return items or [empty]


def _surface_bullet(entry: Mapping[str, Any]) -> str:
    note = entry.get("note")
    suffix = f" — {note}" if note else ""
    return f"- {entry.get('kind')}: `{entry.get('value')}`{suffix}"


def render_readme(manifest: Mapping[str, Any], notes: Optional[str] = None) -> str:
    """Generate the drone's README — always carrying the C2 threat-model statement.

    Generated rather than accepted from the author so the threat model cannot
    be omitted by a drone that forgot it. Prose specific to *this* drone rides
    in ``notes`` and in the manifest's ``when_wrong`` / ``reauthor`` fields.
    """
    author = manifest.get("author", {})
    lines = [
        f"# drone: {manifest.get('name', '')}",
        "",
        str(manifest.get("description", "")),
        "",
        "## What it does",
        "",
        str(manifest.get("purpose", "")),
        "",
        "## Provenance",
        "",
        f"- authored by: `{author.get('model', 'unknown')}`",
        f"- authored on: {author.get('date', 'unknown')}",
        f"- against commit: `{author.get('commit', 'unknown')}`",
        "",
        "## The surface it assumes",
        "",
    ]
    lines += _bullets(
        [_surface_bullet(entry) for entry in manifest.get("assumed_surface") or []],
        empty="- none declared",
    )
    lines += ["", "## Capabilities it declares", ""]
    lines += _bullets(
        [f"- `{capability}`" for capability in manifest.get("capabilities") or []],
        empty="- none declared",
    )
    lines += ["", "## Questions it asks the worker", ""]
    lines += _bullets(
        [
            f"- `{question.get('id')}` — {question.get('prompt')}"
            for question in manifest.get("questions") or []
        ],
        empty="- none — this is a pure code-drone, and costs zero worker tokens",
    )
    lines += ["", "## When it is wrong", ""]
    lines.append(
        str(manifest.get("when_wrong", "")).strip()
        or "Code written against a codebase encodes assumptions that expire. If the "
        "surface above has moved, this drone will keep reporting confidently on a "
        "check that no longer means anything — weigh its age against how fast that "
        "surface changes."
    )
    lines += ["", "## How to re-author it", ""]
    lines.append(
        str(manifest.get("reauthor", "")).strip()
        or f"Re-run the authoring turn and `embodiment drone create "
        f"{manifest.get('name', '')} --force`. Authoring costs one cortex turn; "
        f"re-authoring costs the same, so it is worth doing when the assumed "
        f"surface moves, not on a schedule."
    )
    if notes:
        lines += ["", "## Notes", "", notes.strip()]
    lines += ["", _THREAT_MODEL]
    return "\n".join(lines).rstrip() + "\n"


# ── discovery: the catalog `list` renders ───────────────────────────────────


def find_drones_dir(start: Optional[Path] = None) -> Path:
    """Resolve where drones live: ``$EMBODIMENT_DRONES_DIR``, else ``<repo>/.drones``.

    Repo-scoped by default — a drone is committed and reviewable where it is
    used. Whether a user-scoped ``~/.drones`` should exist alongside is issue
    #45's open question 1 and is deliberately **not** decided here.
    """
    override = os.environ.get(DRONES_DIR_ENV)
    if override:
        return Path(override).expanduser()
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / ".git").exists():
            return candidate / DRONES_DIRNAME
    return here / DRONES_DIRNAME


def humanize_age(authored: str, now: Optional[datetime] = None) -> str:
    """Render an ISO-8601 authoring date as ``12d ago``. Unparseable → ``unknown``."""
    try:
        stamp = datetime.fromisoformat(authored)
    except (TypeError, ValueError):
        return "unknown"
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    seconds = (reference - stamp).total_seconds()
    if seconds < 0:
        return "in the future"
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"


def catalog(
    drones_dir: Path,
    *,
    status_fn: Optional[StatusFn] = None,
    now: Optional[datetime] = None,
) -> list[DroneRecord]:
    """Every drone in *drones_dir*, sorted by name — including broken ones.

    ``list`` is not a directory listing. Its job is **discovery**: answering
    "is there already a drone for this?" with one cheap command instead of an
    authoring turn. A drone that cannot be read is still rendered, with status
    :data:`STATUS_BROKEN` — a drone you cannot see is one you re-author.

    It is also where **staleness becomes visible without being executed**:
    wire ``status_fn=surface_status_fn(root)`` (which is what the ``list`` verb
    does) and each row's status is a re-checked verdict — you learn a drone is
    stale while *choosing* it, not after it has run and reported confidently on
    a surface that moved. With no ``status_fn`` wired every readable drone
    reports :data:`STATUS_UNCHECKED`, because no check ran.
    """
    if not drones_dir.is_dir():
        return []
    records: list[DroneRecord] = []
    for home in sorted(p for p in drones_dir.iterdir() if p.is_dir()):
        records.append(_record_for(home, status_fn=status_fn, now=now))
    return records


def _record_for(
    home: Path, *, status_fn: Optional[StatusFn], now: Optional[datetime]
) -> DroneRecord:
    try:
        drone = load(home.name, home.parent)
    except DroneError as exc:
        return DroneRecord(
            name=home.name,
            description="(manifest unreadable)",
            authored="",
            age="unknown",
            status=STATUS_BROKEN,
            problem=exc.message,
        )
    author = drone.manifest.get("author", {})
    authored = str(author.get("date", ""))
    status = STATUS_UNCHECKED
    problem = ""
    if status_fn is not None:
        try:
            verdict = status_fn(drone)
        # Broad by intent: an injected surface check must not take down `list`.
        except Exception as exc:  # noqa: BLE001
            status = STATUS_BROKEN
            problem = f"status check raised {exc.__class__.__name__}: {exc}"
        else:
            if isinstance(verdict, SurfaceReport):
                status = verdict.status
                # Only a verdict that should change what you do reaches the
                # row's problem line. "3 assumptions hold" is not a problem.
                if verdict.status != STATUS_OK:
                    problem = verdict.summary
            else:
                status = verdict
    return DroneRecord(
        name=drone.name,
        description=drone.description,
        authored=authored,
        age=humanize_age(authored, now),
        status=status,
        model=str(author.get("model", "")),
        commit=str(author.get("commit", "")),
        problem=problem,
    )


_HEADERS = ("name", "does", "authored", "status")


def render_catalog(records: Sequence[DroneRecord]) -> str:
    """Aligned columns: name, one-line description, authoring age, status."""
    if not records:
        return (
            "no drones authored yet\n"
            "author one with: embodiment drone create <name> --source <drone.py> "
            "--description '<one line>'"
        )
    columns = [
        max(len(_HEADERS[0]), *(len(r.name) for r in records)),
        max(len(_HEADERS[1]), *(len(r.description) for r in records)),
        max(len(_HEADERS[2]), *(len(r.age) for r in records)),
    ]
    lines = [
        f"{_HEADERS[0]:<{columns[0]}}  {_HEADERS[1]:<{columns[1]}}  "
        f"{_HEADERS[2]:<{columns[2]}}  {_HEADERS[3]}"
    ]
    for record in records:
        lines.append(
            f"{record.name:<{columns[0]}}  {record.description:<{columns[1]}}  "
            f"{record.age:<{columns[2]}}  {record.status}"
        )
        if record.problem:
            lines.append(f"{'':<{columns[0]}}  └ {record.problem}")
    return "\n".join(lines)
