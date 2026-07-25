"""embodiment.continuity — the shared subprocess adapter to eidetic and coherence.

Issue #2 names the shape: "an embodiment without memory is a sequence of
awakenings; an embodiment without coherence is a sequence of plausible but
potentially different selves." Memory (recall, provenance, ageing) belongs to
``eidetic-cli``; the relationship between memory and the present (quality,
meaning, investiture) belongs to ``coherence-cli``. **This module owns
neither** — no store, no scoring, no embedding logic lives here. It owns only
the seam: a call in, a subprocess out, a parsed and honestly-labelled result
back. Task t14 builds the lifecycle checkpoints (before consequential action /
before final completion / before durable memory) on top of this seam; this
module implements none of that policy.

Why a subprocess adapter, never a dependency (C1)
--------------------------------------------------
``embodiment``'s ``[project].dependencies`` is ``[]`` and must stay that way —
colleague's own ``tests/test_zero_deps.py`` asserts its dependencies are
*exactly* ``["agentfront>=…"]``, so a fat embodiment breaks colleague's CI the
moment colleague imports it. Both continuity subsystems are heavy:
``eidetic-cli`` depends on ``data-refinery-cli[store]``, which transitively
pulls neo4j + pymongo; ``coherence-cli`` depends directly on numpy + httpx.
Neither can ever be a base dependency of this package.

What makes ONE adapter cover BOTH is that they expose byte-identical
agent-first CLI contracts (see both repos' ``cli/_errors.py``): exit codes
``0`` success / ``1`` user error / ``2`` environment error / ``3+`` reserved;
results on stdout, errors/diagnostics on stderr, never mixed; every verb
accepts ``--json``. This module never imports either package — it shells out
to the CLI binary (resolved via ``shutil.which``) with list-form argv, never
``shell=True``, and parses stdout as JSON.

The two silent-corruption traps this module exists to close
-------------------------------------------------------------
1. **eidetic's store resolution is cwd-dependent.** ``eidetic``'s
   ``_resolve_write_dir`` (``eidetic/memory/backend.py``) probes
   ``git rev-parse --show-toplevel`` against the *current working directory*
   to decide where a public record lands: inside a git repo it writes to
   ``<repo-root>/.eidetic/memory`` (**committed**); otherwise (or for a
   private record) it falls back to ``$HOME/.eidetic/memory``. An
   ``EIDETIC_DATA_DIR`` override always wins outright, unconditionally. If
   this adapter shelled out from whatever directory the host process happens
   to be in, a record can land in the wrong repo, or the wrong store,
   silently. Every ``remember``/``recall`` call below therefore REQUIRES the
   caller to supply at least one of ``data_dir`` (sets ``EIDETIC_DATA_DIR``,
   short-circuiting the git-toplevel probe entirely) or ``repo_path`` (an
   explicit subprocess ``cwd``, so eidetic's own probe resolves against a
   directory this adapter chose, never the ambient one). Neither parameter
   defaults to ``os.getcwd()``. Supplying neither is itself treated as a
   degradation (``no-storage-anchor``) rather than an implicit, silent write
   to wherever the process happened to start.

2. **``coherence assess`` exits 0 even when the embedding endpoint is
   unreachable.** It never raises for a dead embedder — quality still ran,
   so the command reports success and names what could not run in the JSON
   payload's ``unavailable`` map (``coherence/assess.py``'s module docstring:
   "Partial availability is success, not failure"). Reading the exit code
   alone would report a broken assessment as a clean pass. :func:`assess`
   below always parses the payload's ``unavailable`` field and — regardless
   of the (successful) exit code — records a :class:`Degradation` when it is
   non-empty. ``AssessOutcome.ok`` stays ``True`` (a usable report WAS
   produced) while ``AssessOutcome.degradation`` makes the partial coverage
   visible to the host, never silent.

Constraint C3 in one sentence: every degradation records a host-visible
transition; nothing here ever degrades silently, and nothing here ever raises
into the host's main path (constraint: never raise into the host — every
public entry point below returns a value, even when the CLI is missing, times
out, exits non-zero, or answers with unparseable JSON).

Scope/visibility conventions (eidetic ``docs/contract.md`` §4)
-----------------------------------------------------------------
eidetic's own contract doc tells every subprocess consumer to pin its
hardcoded scope/visibility flags with its OWN drift test rather than trusting
they happen to agree (§4). This module pins exactly two defaults, both copied
from that document rather than invented independently:

* :data:`DEFAULT_VISIBILITY` ``= "public"`` — §2's ``default_visibility``.
* :data:`SCOPE_NAMING_CONVENTION` — §1's naming rule (documentary: this
  module does not resolve a repo's ``culture.yaml`` suffix itself — that is
  ``embodiment.identity``'s job, one layer up — it only records what
  convention a caller's ``scope`` argument is expected to follow).

``tests/test_continuity.py`` is this module's own drift test per §4.

What t14 builds on top
-----------------------
:func:`probe` gives a checkpoint policy a single, cheap availability read
(``ContinuityStatus.mode`` — ``"full"``, ``"partial"``, or
:data:`NO_CONTINUITY``) without shelling out to either CLI for real work.
:func:`remember`, :func:`recall`, and :func:`assess` are the three seam calls
a checkpoint invokes; none of them decide *when* to fire (before consequential
action / before final completion / before durable memory is t14's lifecycle
policy, not this module's), and none of them decide *what permission* an
action has (coherence answers whether an action makes sense; the capability
layer — outside this module entirely — decides whether it is permitted).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence, Union

__all__ = [
    "EIDETIC_CLI",
    "COHERENCE_CLI",
    "DEFAULT_SCOPE",
    "DEFAULT_VISIBILITY",
    "DEFAULT_TOP_K",
    "SCOPE_NAMING_CONVENTION",
    "PRIVATE_REQUIRES_EXPLICIT_FLAG",
    "FULL_CONTINUITY",
    "PARTIAL_CONTINUITY",
    "NO_CONTINUITY",
    "CODE_CLI_NOT_FOUND",
    "CODE_NO_STORAGE_ANCHOR",
    "CODE_TIMEOUT",
    "CODE_LAUNCH_ERROR",
    "CODE_NONZERO_EXIT",
    "CODE_MALFORMED_JSON",
    "CODE_INVALID_RECORD",
    "CODE_DOMAIN_UNAVAILABLE",
    "Degradation",
    "RememberOutcome",
    "RecallOutcome",
    "AssessOutcome",
    "ContinuityStatus",
    "eidetic_available",
    "coherence_available",
    "probe",
    "remember",
    "recall",
    "assess",
]

_StrPath = Union[str, "os.PathLike[str]"]

# --- CLI binary names (overridable per call for tests / alternate installs) -

EIDETIC_CLI = "eidetic"
COHERENCE_CLI = "coherence"

# --- scope/visibility conventions, pinned per eidetic docs/contract.md §4 --

#: Matches eidetic's own ``--scope`` argparse default (``remember``/``recall``
#: both default to ``"default"``). Not a naming policy — a caller building a
#: checkpoint (t14) is expected to pass the repo's resolved identity scope;
#: this is only the fallback when none is supplied, kept identical to what
#: eidetic itself would already do if this adapter passed no ``--scope`` at
#: all.
DEFAULT_SCOPE = "default"

#: docs/contract.md §2: "Default: `public`, for in-repo team-shared records."
#: Pinned here explicitly (rather than relying on eidetic's own argparse
#: default silently agreeing) so a future divergence is caught by this
#: module's drift test instead of discovered by a misrouted record.
DEFAULT_VISIBILITY = "public"

#: docs/contract.md §1: "One agent-personal scope per repo, named by that
#: repo's culture.yaml top-level agent suffix." Documentary only: this module
#: never reads culture.yaml itself (that is ``embodiment.identity``'s seam,
#: a layer up) — it records the convention so the drift test can pin it and
#: so a caller building a checkpoint knows what a ``scope`` argument means.
SCOPE_NAMING_CONVENTION = "culture.yaml-suffix-per-repo"

#: docs/contract.md §2: private is one explicit ``--visibility private`` away
#: from the public default — never silently inferred. This module honours it
#: by construction: :data:`DEFAULT_VISIBILITY` is ``"public"`` and every
#: private write requires the caller to pass ``visibility="private"``
#: explicitly.
PRIVATE_REQUIRES_EXPLICIT_FLAG = True

DEFAULT_TOP_K = 5

#: Bounds a runaway CLI so a checkpoint can never stall the host loop
#: indefinitely on a hung subprocess.
_DEFAULT_TIMEOUT = 30.0

# --- continuity mode vocabulary (issue #2: "recorded no-continuity mode") --

FULL_CONTINUITY = "full"
PARTIAL_CONTINUITY = "partial"
NO_CONTINUITY = "no-continuity"

# --- degradation code vocabulary (stable, machine-branchable tokens) -------

CODE_CLI_NOT_FOUND = "cli-not-found"
CODE_NO_STORAGE_ANCHOR = "no-storage-anchor"
CODE_TIMEOUT = "timeout"
CODE_LAUNCH_ERROR = "launch-error"
CODE_NONZERO_EXIT = "nonzero-exit"
CODE_MALFORMED_JSON = "malformed-json"
CODE_INVALID_RECORD = "invalid-record"
CODE_DOMAIN_UNAVAILABLE = "domain-unavailable"

# Cap on the reason text lifted from a CLI's stderr/stdout, so a runaway
# traceback dumped by a misbehaving CLI cannot blow up a host's log/artifact.
_MAX_REASON_LEN = 500


@dataclass(frozen=True)
class Degradation:
    """One recorded, host-visible continuity degradation (constraint C3).

    Never inferred implicitly — every non-nominal path through this module
    constructs one of these and returns it on the outcome, rather than
    raising or silently returning an empty/default value that looks like
    success.

    Fields
    ------
    subsystem:
        ``"eidetic"`` or ``"coherence"``.
    stage:
        Which call degraded: ``"remember"``, ``"recall"``, ``"assess"``, or
        ``"probe"``.
    code:
        A stable machine token from the ``CODE_*`` vocabulary above — a
        caller branches on this, never on ``reason``'s free text.
    reason:
        Short human-readable cause, capped to :data:`_MAX_REASON_LEN` chars.
    exit_code:
        The subprocess's exit code, when one was observed (``None`` for a
        degradation that never reached a process, e.g. ``cli-not-found`` or
        ``no-storage-anchor``).
    """

    subsystem: str
    stage: str
    code: str
    reason: str
    exit_code: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "subsystem": self.subsystem,
            "stage": self.stage,
            "code": self.code,
            "reason": self.reason,
        }
        if self.exit_code is not None:
            data["exit_code"] = self.exit_code
        return data


@dataclass(frozen=True)
class RememberOutcome:
    """Result of one :func:`remember` call."""

    ok: bool
    record_id: Optional[str]
    degradation: Optional[Degradation]
    raw: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"ok": self.ok, "record_id": self.record_id}
        if self.degradation is not None:
            data["degradation"] = self.degradation.to_dict()
        return data


@dataclass(frozen=True)
class RecallOutcome:
    """Result of one :func:`recall` call."""

    ok: bool
    records: list[dict[str, Any]]
    degradation: Optional[Degradation]

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"ok": self.ok, "records": list(self.records)}
        if self.degradation is not None:
            data["degradation"] = self.degradation.to_dict()
        return data


@dataclass(frozen=True)
class AssessOutcome:
    """Result of one :func:`assess` call.

    ``ok`` is ``True`` whenever a usable report was produced — including a
    partial one. A partial report (some domain listed in ``unavailable``) is
    still ``ok`` per coherence's own "partial availability is success"
    contract; the trap this type exists to close is that ``ok`` alone must
    NEVER be read as "nothing degraded" — check ``degradation`` for that.
    """

    ok: bool
    domains: dict[str, Any]
    unavailable: dict[str, Any]
    degradation: Optional[Degradation]
    raw: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "ok": self.ok,
            "domains": dict(self.domains),
            "unavailable": dict(self.unavailable),
        }
        if self.degradation is not None:
            data["degradation"] = self.degradation.to_dict()
        return data


@dataclass(frozen=True)
class ContinuityStatus:
    """A cheap availability read across both subsystems — no real work done.

    ``mode`` is the mechanical classification of the two booleans below
    (``FULL_CONTINUITY`` / ``PARTIAL_CONTINUITY`` / ``NO_CONTINUITY``); it is
    NOT a policy decision about what to do in each mode — that is t14's
    lifecycle-checkpoint job, one layer up.
    """

    eidetic_available: bool
    coherence_available: bool
    degradations: tuple[Degradation, ...]

    @property
    def mode(self) -> str:
        if self.eidetic_available and self.coherence_available:
            return FULL_CONTINUITY
        if self.eidetic_available or self.coherence_available:
            return PARTIAL_CONTINUITY
        return NO_CONTINUITY

    def to_dict(self) -> dict[str, Any]:
        return {
            "eidetic_available": self.eidetic_available,
            "coherence_available": self.coherence_available,
            "mode": self.mode,
            "degradations": [d.to_dict() for d in self.degradations],
        }


# ---------------------------------------------------------------------------
# Availability probes
# ---------------------------------------------------------------------------


def eidetic_available(*, eidetic_cli: str = EIDETIC_CLI) -> bool:
    """``True`` iff the ``eidetic`` CLI resolves on ``PATH``."""
    return shutil.which(eidetic_cli) is not None


def coherence_available(*, coherence_cli: str = COHERENCE_CLI) -> bool:
    """``True`` iff the ``coherence`` CLI resolves on ``PATH``."""
    return shutil.which(coherence_cli) is not None


def probe(
    *, eidetic_cli: str = EIDETIC_CLI, coherence_cli: str = COHERENCE_CLI
) -> ContinuityStatus:
    """Cheap availability check for both continuity subsystems.

    Shells out to nothing — a ``shutil.which`` lookup only. Missing subsystem
    absence is itself a recorded :class:`Degradation` (never a silent False),
    so a caller reading only ``ContinuityStatus.degradations`` still sees
    what is missing without inspecting the booleans separately.
    """
    eidetic_ok = eidetic_available(eidetic_cli=eidetic_cli)
    coherence_ok = coherence_available(coherence_cli=coherence_cli)
    degradations: list[Degradation] = []
    if not eidetic_ok:
        degradations.append(
            Degradation(
                subsystem="eidetic",
                stage="probe",
                code=CODE_CLI_NOT_FOUND,
                reason=f"{eidetic_cli!r} not found on PATH",
            )
        )
    if not coherence_ok:
        degradations.append(
            Degradation(
                subsystem="coherence",
                stage="probe",
                code=CODE_CLI_NOT_FOUND,
                reason=f"{coherence_cli!r} not found on PATH",
            )
        )
    return ContinuityStatus(
        eidetic_available=eidetic_ok,
        coherence_available=coherence_ok,
        degradations=tuple(degradations),
    )


# ---------------------------------------------------------------------------
# Subprocess plumbing (private) — the ONE place argv is ever executed
# ---------------------------------------------------------------------------


def _run(
    argv: Sequence[str],
    *,
    cwd: Optional[str],
    env: Mapping[str, str],
    timeout: float,
) -> tuple[Optional[int], str, str, Optional[str]]:
    """Run *argv* and return ``(returncode, stdout, stderr, launch_error)``.

    Never raises: a timeout or a launch failure (missing cwd, unreadable
    binary, …) is reported back as ``launch_error`` text instead of
    propagating ``subprocess.TimeoutExpired`` / ``OSError`` into the caller.
    List-form argv, no shell — the subprocess is never handed a shell string.
    """
    try:
        proc = subprocess.run(
            list(argv),
            cwd=cwd,
            env=dict(env),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None, "", "", "timeout"
    except OSError as exc:
        return None, "", "", f"failed to launch: {exc}"
    return proc.returncode, proc.stdout or "", proc.stderr or "", None


def _launch_error_degradation(*, subsystem: str, stage: str, launch_error: str) -> Degradation:
    if launch_error == "timeout":
        return Degradation(
            subsystem=subsystem,
            stage=stage,
            code=CODE_TIMEOUT,
            reason=f"{subsystem} CLI timed out",
        )
    return Degradation(
        subsystem=subsystem,
        stage=stage,
        code=CODE_LAUNCH_ERROR,
        reason=launch_error[:_MAX_REASON_LEN],
    )


def _nonzero_exit_degradation(
    *, subsystem: str, stage: str, returncode: int, stdout: str, stderr: str
) -> Degradation:
    reason = (stderr or stdout or f"{subsystem} exited {returncode}").strip()
    return Degradation(
        subsystem=subsystem,
        stage=stage,
        code=CODE_NONZERO_EXIT,
        reason=reason[:_MAX_REASON_LEN],
        exit_code=returncode,
    )


def _parse_json(stdout: str) -> tuple[Any, Optional[str]]:
    """Best-effort JSON parse; returns ``(value, None)`` or ``(None, reason)``."""
    try:
        return json.loads(stdout), None
    except json.JSONDecodeError as exc:
        return None, f"could not parse CLI JSON output: {exc}"


def _build_env(
    data_dir: Optional[_StrPath], extra_env: Optional[Mapping[str, str]]
) -> dict[str, str]:
    """Build the child environment, pinning ``EIDETIC_DATA_DIR`` LAST.

    Starts from the current process environment (so ``PATH`` and everything
    else an operator already exported survives), layers any caller-supplied
    *extra_env* on top (an injection seam for e.g. a discovered embedder
    endpoint), then — when *data_dir* is given — sets ``EIDETIC_DATA_DIR``
    unconditionally as the final write. This ordering is deliberate: this
    module's own cwd-safety pin (trap #1) can never be silently shadowed by
    an *extra_env* mapping a caller passed in for an unrelated purpose.
    """
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    if data_dir is not None:
        env["EIDETIC_DATA_DIR"] = str(data_dir)
    return env


def _check_storage_anchor(
    data_dir: Optional[_StrPath], repo_path: Optional[_StrPath], *, stage: str
) -> Optional[Degradation]:
    """Refuse an eidetic call with neither ``data_dir`` nor ``repo_path``.

    This is the enforcement point for trap #1: eidetic's store resolution is
    cwd-dependent, so shelling out with an unpinned cwd risks a public record
    landing in whatever repo the host process happened to be started from.
    Neither parameter defaults to ``os.getcwd()`` anywhere in this module —
    the caller must say where the write/read is anchored.
    """
    if data_dir is None and repo_path is None:
        return Degradation(
            subsystem="eidetic",
            stage=stage,
            code=CODE_NO_STORAGE_ANCHOR,
            reason=(
                "neither data_dir nor repo_path was supplied; refusing to shell out "
                "with an ambiguous cwd (eidetic's store resolution is cwd-dependent)"
            ),
        )
    return None


# ---------------------------------------------------------------------------
# eidetic seam — remember / recall
# ---------------------------------------------------------------------------


def remember(
    record: Mapping[str, Any],
    *,
    scope: str = DEFAULT_SCOPE,
    visibility: str = DEFAULT_VISIBILITY,
    data_dir: Optional[_StrPath] = None,
    repo_path: Optional[_StrPath] = None,
    eidetic_cli: str = EIDETIC_CLI,
    timeout: float = _DEFAULT_TIMEOUT,
    env: Optional[Mapping[str, str]] = None,
) -> RememberOutcome:
    """Ingest *record* via ``eidetic remember --json``.

    *record* must already be shaped per eidetic's own contract (``id``,
    ``text``, ``type`` keys — see ``eidetic/cli/_commands/remember.py``); this
    adapter shapes no records itself, it only serialises and ships whatever
    mapping the caller built (a checkpoint policy's job, one layer up).

    One of *data_dir* or *repo_path* is REQUIRED (trap #1 above) — passing
    neither degrades to :data:`CODE_NO_STORAGE_ANCHOR` without shelling out at
    all. Passing *data_dir* sets ``EIDETIC_DATA_DIR`` (bypassing eidetic's
    git-toplevel probe outright); passing *repo_path* pins the subprocess
    ``cwd`` so that probe resolves against a directory this call chose.
    Passing both is fine — ``EIDETIC_DATA_DIR`` always wins per eidetic's own
    resolution order, and the explicit ``cwd`` is harmless alongside it.

    Never raises: a missing CLI, a launch failure, a timeout, a non-zero
    exit, or unparseable/malformed JSON all degrade to
    ``RememberOutcome(ok=False, ...)`` with a :class:`Degradation` attached.
    """
    anchor_error = _check_storage_anchor(data_dir, repo_path, stage="remember")
    if anchor_error is not None:
        return RememberOutcome(ok=False, record_id=None, degradation=anchor_error)

    cli_path = shutil.which(eidetic_cli)
    if cli_path is None:
        return RememberOutcome(
            ok=False,
            record_id=None,
            degradation=Degradation(
                subsystem="eidetic",
                stage="remember",
                code=CODE_CLI_NOT_FOUND,
                reason=f"{eidetic_cli!r} not found on PATH",
            ),
        )

    try:
        record_json = json.dumps(dict(record))
    except (TypeError, ValueError) as exc:
        return RememberOutcome(
            ok=False,
            record_id=None,
            degradation=Degradation(
                subsystem="eidetic",
                stage="remember",
                code=CODE_INVALID_RECORD,
                reason=str(exc)[:_MAX_REASON_LEN],
            ),
        )

    argv = [
        cli_path,
        "remember",
        record_json,
        "--scope",
        scope,
        "--visibility",
        visibility,
        "--json",
    ]
    child_env = _build_env(data_dir, env)
    child_cwd = str(repo_path) if repo_path is not None else None

    returncode, stdout, stderr, launch_error = _run(
        argv, cwd=child_cwd, env=child_env, timeout=timeout
    )
    if launch_error is not None:
        return RememberOutcome(
            ok=False,
            record_id=None,
            degradation=_launch_error_degradation(
                subsystem="eidetic", stage="remember", launch_error=launch_error
            ),
        )
    if returncode != 0:
        return RememberOutcome(
            ok=False,
            record_id=None,
            degradation=_nonzero_exit_degradation(
                subsystem="eidetic",
                stage="remember",
                returncode=returncode,
                stdout=stdout,
                stderr=stderr,
            ),
        )

    payload, parse_err = _parse_json(stdout)
    if parse_err is not None or not isinstance(payload, dict):
        reason = parse_err or "expected a JSON object from 'eidetic remember --json'"
        return RememberOutcome(
            ok=False,
            record_id=None,
            degradation=Degradation(
                subsystem="eidetic",
                stage="remember",
                code=CODE_MALFORMED_JSON,
                reason=reason[:_MAX_REASON_LEN],
            ),
        )

    ids = payload.get("ids")
    record_id = ids[0] if isinstance(ids, list) and ids else None
    return RememberOutcome(ok=True, record_id=record_id, degradation=None, raw=payload)


def recall(
    query: str,
    *,
    scope: str = DEFAULT_SCOPE,
    visibility: str = DEFAULT_VISIBILITY,
    top_k: int = DEFAULT_TOP_K,
    data_dir: Optional[_StrPath] = None,
    repo_path: Optional[_StrPath] = None,
    eidetic_cli: str = EIDETIC_CLI,
    timeout: float = _DEFAULT_TIMEOUT,
    env: Optional[Mapping[str, str]] = None,
) -> RecallOutcome:
    """Search the memory store via ``eidetic recall --json``.

    Same storage-anchor requirement as :func:`remember` (trap #1): one of
    *data_dir* or *repo_path* is REQUIRED. ``eidetic recall --json`` emits a
    JSON **list** of record dicts (not an object) — this adapter passes that
    list through verbatim, ranking/relevance scoring stays entirely inside
    eidetic; this module reads no ``score`` field and computes none.

    Never raises — see :func:`remember`'s docstring for the full degradation
    ladder, which this function mirrors.
    """
    anchor_error = _check_storage_anchor(data_dir, repo_path, stage="recall")
    if anchor_error is not None:
        return RecallOutcome(ok=False, records=[], degradation=anchor_error)

    cli_path = shutil.which(eidetic_cli)
    if cli_path is None:
        return RecallOutcome(
            ok=False,
            records=[],
            degradation=Degradation(
                subsystem="eidetic",
                stage="recall",
                code=CODE_CLI_NOT_FOUND,
                reason=f"{eidetic_cli!r} not found on PATH",
            ),
        )

    argv = [
        cli_path,
        "recall",
        query,
        "--top-k",
        str(top_k),
        "--scope",
        scope,
        "--visibility",
        visibility,
        "--json",
    ]
    child_env = _build_env(data_dir, env)
    child_cwd = str(repo_path) if repo_path is not None else None

    returncode, stdout, stderr, launch_error = _run(
        argv, cwd=child_cwd, env=child_env, timeout=timeout
    )
    if launch_error is not None:
        return RecallOutcome(
            ok=False,
            records=[],
            degradation=_launch_error_degradation(
                subsystem="eidetic", stage="recall", launch_error=launch_error
            ),
        )
    if returncode != 0:
        return RecallOutcome(
            ok=False,
            records=[],
            degradation=_nonzero_exit_degradation(
                subsystem="eidetic",
                stage="recall",
                returncode=returncode,
                stdout=stdout,
                stderr=stderr,
            ),
        )

    payload, parse_err = _parse_json(stdout)
    if parse_err is not None or not isinstance(payload, list):
        reason = parse_err or "expected a JSON array from 'eidetic recall --json'"
        return RecallOutcome(
            ok=False,
            records=[],
            degradation=Degradation(
                subsystem="eidetic",
                stage="recall",
                code=CODE_MALFORMED_JSON,
                reason=reason[:_MAX_REASON_LEN],
            ),
        )

    records = [entry for entry in payload if isinstance(entry, dict)]
    return RecallOutcome(ok=True, records=records, degradation=None)


# ---------------------------------------------------------------------------
# coherence seam — assess
# ---------------------------------------------------------------------------


def assess(
    path: _StrPath,
    *,
    coherence_cli: str = COHERENCE_CLI,
    timeout: float = _DEFAULT_TIMEOUT,
    env: Optional[Mapping[str, str]] = None,
) -> AssessOutcome:
    """Run every applicable coherence domain on *path* via ``coherence assess --json``.

    Trap #2 (the point of this function): ``coherence assess`` exits ``0``
    even when the embedding endpoint is unreachable — it names the affected
    domain(s) in the JSON payload's ``unavailable`` map instead of failing
    the process. This function ALWAYS parses that field and records a
    :class:`Degradation` (``code=`` :data:`CODE_DOMAIN_UNAVAILABLE`) whenever
    it is non-empty, regardless of the (successful) exit code —
    ``AssessOutcome.ok`` alone must never be read as "nothing degraded".

    Unlike :func:`remember`/:func:`recall`, no storage anchor is required
    here: coherence reads *path* and talks to an HTTP embedding endpoint; it
    has no eidetic-style cwd-dependent store to land in.

    Never raises — a missing CLI, launch failure, timeout, non-zero exit
    (coherence's own contract reserves that for a genuine file I/O error —
    see ``coherence/cli/_commands/assess.py``'s module docstring), or
    unparseable JSON all degrade to ``AssessOutcome(ok=False, ...)``.
    """
    cli_path = shutil.which(coherence_cli)
    if cli_path is None:
        return AssessOutcome(
            ok=False,
            domains={},
            unavailable={},
            degradation=Degradation(
                subsystem="coherence",
                stage="assess",
                code=CODE_CLI_NOT_FOUND,
                reason=f"{coherence_cli!r} not found on PATH",
            ),
        )

    argv = [cli_path, "assess", str(path), "--json"]
    child_env = _build_env(None, env)

    returncode, stdout, stderr, launch_error = _run(argv, cwd=None, env=child_env, timeout=timeout)
    if launch_error is not None:
        return AssessOutcome(
            ok=False,
            domains={},
            unavailable={},
            degradation=_launch_error_degradation(
                subsystem="coherence", stage="assess", launch_error=launch_error
            ),
        )
    if returncode != 0:
        return AssessOutcome(
            ok=False,
            domains={},
            unavailable={},
            degradation=_nonzero_exit_degradation(
                subsystem="coherence",
                stage="assess",
                returncode=returncode,
                stdout=stdout,
                stderr=stderr,
            ),
        )

    payload, parse_err = _parse_json(stdout)
    if parse_err is not None or not isinstance(payload, dict):
        reason = parse_err or "expected a JSON object from 'coherence assess --json'"
        return AssessOutcome(
            ok=False,
            domains={},
            unavailable={},
            degradation=Degradation(
                subsystem="coherence",
                stage="assess",
                code=CODE_MALFORMED_JSON,
                reason=reason[:_MAX_REASON_LEN],
            ),
        )

    raw_unavailable = payload.get("unavailable")
    unavailable = raw_unavailable if isinstance(raw_unavailable, dict) else {}
    raw_domains = payload.get("domains")
    domains = raw_domains if isinstance(raw_domains, dict) else {}

    degradation: Optional[Degradation] = None
    if unavailable:
        # Trap #2: exit 0 already happened above — that is exactly why this
        # branch must run unconditionally on the parsed payload, never on the
        # exit code. A verdict with SOME domain unavailable is still ``ok``
        # (a usable, partial report), but the degradation makes the gap
        # visible to the host instead of silently passing as a clean run.
        names = ", ".join(sorted(unavailable))
        degradation = Degradation(
            subsystem="coherence",
            stage="assess",
            code=CODE_DOMAIN_UNAVAILABLE,
            reason=f"domain(s) unavailable: {names}",
        )

    return AssessOutcome(
        ok=True,
        domains=domains,
        unavailable=unavailable,
        degradation=degradation,
        raw=payload,
    )
