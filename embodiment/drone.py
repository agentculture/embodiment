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

No escalation (c46)
-------------------
A drone that hits a case it cannot decide returns **"I cannot"**
(:attr:`DroneAnswer.cannot`). There is no escalate-to-cortex path in v1 — that
is the whole point of the cost model. The record-keeping for refusals belongs
to the safeguards task (t12), not here.

Seams deliberately left open for the safeguards task (t12)
----------------------------------------------------------
* **opt-in** — nothing in this module consults an opt-in switch. t12 owns the
  guard that makes a fresh checkout evoke nothing.
* **staleness** — :func:`catalog` renders a ``status`` column computed by an
  injected ``status_fn``. With none wired the status is
  :data:`STATUS_UNCHECKED`, which is the honest answer: no check ran. t12
  supplies the assumed-surface re-check that turns it into ``ok`` / ``STALE``.
* **audit trail** — :func:`invoke` *returns* an :class:`Evocation` (it does not
  raise on a failed run) carrying the drone name, the source content hash, the
  declared capabilities and every :class:`DroneCall`'s acceptance. t12 owns
  writing those records; this module owns producing them.
"""

from __future__ import annotations

import hashlib
import importlib.util
import itertools
import json
import os
import re
import shutil
import subprocess  # nosec B404 - fixed argv, no shell; reads the authoring commit
import sys
import tempfile
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

__all__ = [
    "AskFn",
    "DRONES_DIRNAME",
    "DRONE_ENTRYPOINT",
    "DRONE_STATUSES",
    "Drone",
    "DroneAnswer",
    "DroneCall",
    "DroneError",
    "DroneRecord",
    "DroneRequest",
    "Evocation",
    "MANIFEST_SCHEMA_VERSION",
    "SmokeResult",
    "STATUS_BROKEN",
    "STATUS_UNCHECKED",
    "StatusFn",
    "UndeclaredQuestion",
    "catalog",
    "create",
    "find_drones_dir",
    "git_commit",
    "humanize_age",
    "invoke",
    "load",
    "mapping_ask",
    "no_worker_ask",
    "render_catalog",
    "render_readme",
    "smoke",
    "source_hash",
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

#: The module-level callable every ``drone.py`` must define:
#: ``run(request: DroneRequest) -> DroneAnswer``.
DRONE_ENTRYPOINT = "run"

#: Environment override for where drones live. ``--drones-dir`` beats it.
DRONES_DIR_ENV = "EMBODIMENT_DRONES_DIR"

# ── statuses ────────────────────────────────────────────────────────────────

#: No assumed-surface check ran. The default, and deliberately not ``"ok"``:
#: reporting a drone healthy because nobody looked is exactly the confident
#: false claim this repo's degradation rule (C3) exists to prevent.
STATUS_UNCHECKED = "unchecked"

#: The directory exists but its manifest could not be read or does not
#: validate. Rendered as a row rather than swallowed, so a broken drone is
#: visible at the moment you are choosing one.
STATUS_BROKEN = "broken"

#: The vocabulary this task ships. t12's staleness re-check adds its own.
DRONE_STATUSES = (STATUS_UNCHECKED, STATUS_BROKEN)

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


@dataclass(frozen=True)
class Evocation:
    """The record of one run — produced whether it succeeded, refused or failed.

    ``ok`` is False only for a *harness* failure (the drone raised, had no
    entry point, or returned nothing usable). A clean "I cannot" is ``ok`` with
    ``cannot`` set: refusing is working correctly, not failing.
    """

    name: str
    source_sha256: str
    capabilities: tuple[str, ...]
    calls: tuple[DroneCall, ...]
    ok: bool
    answer: Optional[str] = None
    cannot: Optional[str] = None
    detail: Mapping[str, Any] = field(default_factory=dict)
    failure: str = ""

    @property
    def calls_accepted(self) -> int:
        return sum(1 for call in self.calls if call.accepted)

    @property
    def call_acceptance(self) -> Optional[float]:
        """Accepted / asked, or ``None`` when nothing was asked.

        ``None`` rather than ``1.0``: a drone that asked nothing has no
        acceptance rate, and reporting a perfect one would be the same
        confident-about-nothing claim the whole module argues against.
        """
        if not self.calls:
            return None
        return self.calls_accepted / len(self.calls)


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


#: ``status_fn(drone) -> status``. t12 wires the assumed-surface re-check here.
StatusFn = Callable[[Drone], str]


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
    if declared is not None:
        expected = _JSON_TYPES.get(declared)
        if expected is None:
            return f"declared type {declared!r} is not one of {sorted(_JSON_TYPES)}"
        # bool is a subclass of int in Python; JSON does not agree.
        if declared in ("integer", "number") and isinstance(value, bool):
            return f"expected {declared}, got boolean"
        if not isinstance(value, expected):
            return f"expected {declared}, got {type(value).__name__}"
    if "enum" in schema:
        allowed = schema["enum"]
        if not isinstance(allowed, list):
            return "enum must be a list"
        if value not in allowed:
            return f"{value!r} is not one of the declared enum values"
    if declared == "array" and isinstance(schema.get("items"), Mapping):
        for index, item in enumerate(value):
            reason = validate_answer(item, schema["items"])
            if reason:
                return f"item {index}: {reason}"
    if declared == "object":
        for key in schema.get("required") or ():
            if key not in value:
                return f"missing required key {key!r}"
        properties = schema.get("properties")
        if isinstance(properties, Mapping):
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
    for entry in _require(data, "assumed_surface", list, ""):
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
    capabilities = _require(data, "capabilities", list, "")
    for capability in capabilities:
        if not isinstance(capability, str) or not capability.strip():
            raise DroneError(
                "every declared capability must be a non-blank string",
                "capabilities are a declaration for review, not an enforcement "
                "boundary — see the threat model in the drone's README",
            )
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
    """SHA-256 of the drone source, for the evocation audit trail t12 writes."""
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


# ── running ─────────────────────────────────────────────────────────────────


def _load_entrypoint(source: Path, name: str) -> Callable[[DroneRequest], Any]:
    """Import *source* under a fresh name and return its ``run``.

    A **unique** module name per load, so re-authoring a drone in a
    long-running host never resolves to a stale cached module — but the entry
    is then removed from ``sys.modules`` again. The registration is only needed
    *during* ``exec_module`` (dataclasses and similar machinery look the module
    up by name while the body executes); leaving it behind would grow
    ``sys.modules`` by one entry per evocation, and a library that leaks in
    proportion to how often its cheap verb is called is a poor bargain. The
    returned function keeps its own globals alive through ``__globals__``.
    """
    module_name = f"_embodiment_drone_{re.sub(r'[^a-z0-9_]', '_', name)}_{next(_MODULE_COUNTER)}"
    spec = importlib.util.spec_from_file_location(module_name, source)
    if spec is None or spec.loader is None:
        raise DroneError(
            f"cannot load {source} as a Python module",
            "drone.py must be an importable Python source file",
        )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        try:
            spec.loader.exec_module(module)
        except Exception as exc:  # noqa: BLE001 - model-written code; report, never traceback
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


def invoke(
    drone: Drone,
    *,
    root: Path,
    args: Optional[Mapping[str, str]] = None,
    ask: AskFn = no_worker_ask,
) -> Evocation:
    """Run *drone* and return the record — including for a failed run.

    Never raises for a failure inside the drone: a harness that raises cannot
    hand t12 a record of what went wrong, and C3 requires every degradation to
    be observable to the host rather than escaping as an exception.

    **This imports and executes model-written Python in this process.** See the
    module docstring's threat model.
    """
    questions = {str(q["id"]): q for q in drone.questions}
    recorder = _RecordingAsk(questions, ask)
    sha = source_hash(drone.source)
    capabilities = drone.capabilities

    def _fail(message: str) -> Evocation:
        return Evocation(
            name=drone.name,
            source_sha256=sha,
            capabilities=capabilities,
            calls=tuple(recorder.calls),
            ok=False,
            failure=message,
        )

    try:
        entrypoint = _load_entrypoint(drone.source, drone.name)
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
        raw = entrypoint(request)
    except UndeclaredQuestion as exc:
        return _fail(exc.message)
    except Exception as exc:  # noqa: BLE001 - model-written code; report, never traceback
        where = traceback.extract_tb(exc.__traceback__)[-1]
        return _fail(
            f"{DRONE_ENTRYPOINT}() raised {exc.__class__.__name__}: {exc} "
            f"(at {Path(where.filename).name}:{where.lineno})"
        )

    try:
        answer = _coerce_answer(raw)
    except DroneError as exc:
        return _fail(exc.message)

    has_answer = bool(answer.answer and str(answer.answer).strip())
    has_refusal = bool(answer.cannot and str(answer.cannot).strip())
    if not has_answer and not has_refusal:
        return _fail(
            f"{DRONE_ENTRYPOINT}() returned neither an answer nor an 'I cannot' — "
            "a drone that reports nothing has not run, it has only been shaped"
        )

    return Evocation(
        name=drone.name,
        source_sha256=sha,
        capabilities=capabilities,
        calls=tuple(recorder.calls),
        ok=True,
        answer=answer.answer if has_answer else None,
        cannot=answer.cannot if has_refusal else None,
        detail=dict(answer.detail),
    )


def smoke(drone: Drone, *, root: Path) -> SmokeResult:
    """Prove the drone actually runs, using only its manifest's canned answers.

    Hermetic by construction: every declared question carries a canned answer
    validated against its own schema, so no worker is dialled. Passing means
    the code imports, the entry point exists, the run completes, every question
    it asked was declared, and it returned an answer or a refusal.
    """
    smoke_block = drone.manifest.get("smoke", {})
    evocation = invoke(
        drone,
        root=root,
        args=smoke_block.get("args", {}),
        ask=mapping_ask(smoke_block.get("answers", {})),
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
        result = smoke(candidate, root=root)
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
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(staged), str(target))
    finally:
        shutil.rmtree(staging_parent, ignore_errors=True)

    return Drone(name=name, home=target, manifest=manifest)


def _write_artifacts(
    home: Path, manifest: Mapping[str, Any], source_text: str, notes: Optional[str]
) -> None:
    (home / MANIFEST_FILENAME).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (home / SOURCE_FILENAME).write_text(source_text, encoding="utf-8")
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
"""


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
    surface = manifest.get("assumed_surface") or []
    if surface:
        for entry in surface:
            note = entry.get("note")
            suffix = f" — {note}" if note else ""
            lines.append(f"- {entry.get('kind')}: `{entry.get('value')}`{suffix}")
    else:
        lines.append("- none declared")
    lines += ["", "## Capabilities it declares", ""]
    capabilities = manifest.get("capabilities") or []
    if capabilities:
        lines += [f"- `{capability}`" for capability in capabilities]
    else:
        lines.append("- none declared")
    lines += ["", "## Questions it asks the worker", ""]
    questions = manifest.get("questions") or []
    if questions:
        for question in questions:
            lines.append(f"- `{question.get('id')}` — {question.get('prompt')}")
    else:
        lines.append("- none — this is a pure code-drone, and costs zero worker tokens")
    lines += ["", "## When it is wrong", ""]
    when_wrong = str(manifest.get("when_wrong", "")).strip()
    lines.append(
        when_wrong
        or "Code written against a codebase encodes assumptions that expire. If the "
        "surface above has moved, this drone will keep reporting confidently on a "
        "check that no longer means anything — weigh its age against how fast that "
        "surface changes."
    )
    lines += ["", "## How to re-author it", ""]
    reauthor = str(manifest.get("reauthor", "")).strip()
    lines.append(
        reauthor
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

    *status_fn* is the seam t12 fills with the assumed-surface re-check. With
    none wired every readable drone reports :data:`STATUS_UNCHECKED`.
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
            status = status_fn(drone)
        except Exception as exc:  # noqa: BLE001 - an injected check must not break `list`
            status = STATUS_BROKEN
            problem = f"status check raised {exc.__class__.__name__}: {exc}"
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
