"""embodiment.continuity — the in-process seam to eidetic and coherence.

Issue #2 names the shape: "an embodiment without memory is a sequence of
awakenings; an embodiment without coherence is a sequence of plausible but
potentially different selves." Memory (recall, provenance, ageing) belongs to
``eidetic-cli``; the relationship between memory and the present (quality,
meaning, investiture) belongs to ``coherence-cli``. **This module owns
neither** — no store, no scoring, no embedding logic lives here. It owns only
the seam: a call in, a sibling library call out, and an honestly-labelled
result back. Task t14 builds the lifecycle checkpoints (before consequential
action / before final completion / before durable memory) on top of this seam;
this module implements none of that policy.

Deviation d2 — why these are base dependencies now
---------------------------------------------------
This module used to shell out to the ``eidetic`` and ``coherence`` binaries
precisely so that ``embodiment``'s ``[project].dependencies`` could stay ``[]``
(constraint C1). **Deviation d2 reversed that decision, deliberately and with
the cost stated up front.** Both packages are now ordinary base dependencies,
imported at module scope and called directly.

What that buys: real Python objects instead of a JSON round-trip, an injectable
``embed_fn`` (so coherence can be exercised offline), no process-spawn cost per
boundary, and — most importantly — type-checkable, greppable coupling instead
of an argv contract that drifts silently.

What it costs, and this is not hypothetical:

* ``pip install embodiment`` now pulls ``eidetic-cli`` →
  ``data-refinery-cli[store]`` → **neo4j + pymongo**, plus ``coherence-cli`` →
  **numpy + httpx**, plus ``events-cli`` → **paho-mqtt**.
* Importing ``embodiment.continuity`` now transitively imports third-party
  modules. colleague's ``tests/test_zero_deps.py`` asserts its dependencies are
  *exactly* ``["agentfront>=…"]`` **and** that importing colleague adds no
  third-party top-level import — so **colleague cannot adopt embodiment until
  C1b is answered**. Whether colleague relaxes its one-base-dependency rule is
  now a hard prerequisite for the seam proposal, not an open question.

``tests/test_zero_deps.py`` in this repo is the human gate on that list: the
approved dependency set and the exact set of third-party modules an import
introduces are both pinned, and *any* delta in either direction fails until a
human updates the pin on purpose.

Each subsystem is still **optional at runtime**. The module-scope imports are
guarded, so a missing or broken ``eidetic``/``coherence`` install degrades to a
recorded no-continuity mode instead of breaking ``import embodiment``.

The two silent-corruption traps, restated for in-process
----------------------------------------------------------
The traps did not go away when the subprocess did. They changed shape.

1. **eidetic's store resolution is cwd-dependent, and there is no longer a
   subprocess cwd to pin.** ``eidetic``'s ``_resolve_write_dir``
   (``eidetic/memory/backend.py``) resolves in this order: an explicit
   ``EIDETIC_DATA_DIR`` override wins unconditionally; otherwise a *public*
   record inside a git repo lands in ``<repo-root>/.eidetic/memory``
   (**committed, team-shared**); otherwise ``$HOME/.eidetic/memory``. The
   git-toplevel probe runs against ``os.getcwd()`` — which, in-process, is the
   **host application's** cwd. A host that happens to start inside a git
   checkout would silently commit its agent's memories into that repo.

   Over the CLI this was controllable two ways: ``EIDETIC_DATA_DIR``, or an
   explicit subprocess ``cwd``. In-process only the first survives — pinning
   ``os.chdir()`` would be a process-global race, and computing
   ``<repo>/.eidetic/memory`` here would mean encoding eidetic's private store
   layout in embodiment, which is exactly the "reimplement, don't compose"
   failure this module exists to avoid. So **``data_dir`` is the sole anchor
   and it is mandatory**: :func:`remember` and :func:`recall` refuse — with a
   recorded :data:`CODE_NO_STORAGE_ANCHOR` degradation, before touching
   eidetic at all — when it is absent. It never defaults to ``os.getcwd()``.
   The pin is applied through :func:`_pinned_store`, which sets the override,
   holds it for exactly the duration of the call, and restores the host's
   environment afterwards in a ``finally`` (see that function for the residual
   process-global caveat).

2. **``coherence.assess`` reports partial availability in its payload, not its
   return.** Over the CLI it exited ``0`` with an ``unavailable`` map naming
   what could not run. In-process the same fact wears different clothes: the
   call **returns normally** and names the gap in the returned dict. A dead
   embedding endpoint is a *successful* return carrying
   ``unavailable={"meaning": …, "investiture": …}`` (``coherence/assess.py``:
   "Partial availability is success, not failure"). **Never infer success from
   "it didn't raise."** :func:`assess` always reads the returned structure and
   records a :class:`Degradation` when ``unavailable`` is non-empty.
   ``AssessOutcome.ok`` stays ``True`` (a usable report WAS produced) while
   ``AssessOutcome.degradation`` makes the partial coverage visible to the
   host, never silent.

Where the in-process API differs from what the CLI contract implied
--------------------------------------------------------------------
Documented rather than papered over:

* **``repo_path`` is gone.** It meant "run eidetic's git probe from here",
  which required a subprocess cwd. See trap #1.
* **``Degradation.exit_code`` is gone**, replaced by
  :attr:`Degradation.exception` (the exception class name). There is no exit
  code in-process, and inventing one would be a lie.
* **There is no timeout.** The subprocess adapter bounded a hung CLI with
  ``subprocess.run(timeout=…)``. An in-process call cannot be bounded without
  a watchdog thread or a signal, and this module will not pretend otherwise.
  A host that needs a hard bound must impose it at its own boundary.
* **coherence's file I/O errors now surface here.** ``coherence.assess.assess``
  deliberately lets ``FileNotFoundError``/``IsADirectoryError``/
  ``UnicodeDecodeError``/``OSError`` propagate — converting them is "the CLI's
  job, not this engine's". In-process **embodiment is that boundary**, so it
  catches them and records :data:`CODE_ARTIFACT_UNREADABLE`.
* **``added_by`` is never inferred.** eidetic's ``remember`` CLI stamps it from
  *its own* ``culture.yaml``; reached in-process that would stamp eidetic's
  identity onto embodiment's records. The caller supplies it, or it stays
  ``None`` — identity resolution is :mod:`embodiment.identity`'s job, a layer up.
* **``lifecycle`` rides through on every path.** eidetic's CLI honours it only
  on the inline-scope branch; this seam threads it uniformly.

Constraint C3 in one sentence: every degradation records a host-visible
transition; nothing here degrades silently, and nothing here raises into the
host's main path — every public entry point returns a value even when a
subsystem is missing, misconfigured, raises, or answers with the wrong shape.
``KeyboardInterrupt``/``SystemExit`` are the deliberate exception: "never
raise" means never raise *errors*, not never yield control.

Scope/visibility conventions (eidetic ``docs/contract.md`` §4)
-----------------------------------------------------------------
eidetic's contract doc tells every consumer to pin its hardcoded
scope/visibility defaults with its OWN drift test rather than trusting they
happen to agree (§4). Importing eidetic instead of shelling out to it does not
retire that advice — it raises the stakes, because the flags are now Python
defaults rather than argv this module builds. Two defaults are pinned, both
copied from that document rather than invented independently:

* :data:`DEFAULT_VISIBILITY` ``= "public"`` — §2's ``default_visibility``.
* :data:`SCOPE_NAMING_CONVENTION` — §1's naming rule (documentary: this module
  does not resolve a repo's ``culture.yaml`` suffix itself — that is
  :mod:`embodiment.identity`'s job — it only records what convention a caller's
  ``scope`` argument is expected to follow).

``tests/test_continuity.py`` is this module's own drift test per §4.

What t14 builds on top
-----------------------
:func:`probe` gives a checkpoint policy a single, cheap availability read
(``ContinuityStatus.mode`` — :data:`FULL_CONTINUITY`, :data:`PARTIAL_CONTINUITY`
or :data:`NO_CONTINUITY`) without doing any real work. :func:`remember`,
:func:`recall` and :func:`assess` are the three seam calls a checkpoint invokes;
none of them decide *when* to fire (that is t14's lifecycle policy), and none of
them decide *what permission* an action has (coherence answers whether an action
makes sense; the capability layer — outside this module entirely — decides
whether it is permitted).
"""

from __future__ import annotations

import copy
import os
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Optional, Union

# --- the d2 imports: module scope, guarded, never lazy ----------------------
#
# Module scope IS the deviation's content — d2 chose base dependencies over the
# optional-extra/lazy-import alternative. The guard is not a hedge against that
# choice; it is what keeps each subsystem individually optional at *runtime*, so
# a broken sibling install degrades to a recorded no-continuity mode instead of
# breaking ``import embodiment`` for a host that never touches continuity.
# ``Exception`` rather than ``ImportError``: a half-installed dependency can
# fail at module-exec time in ways that are not ImportError, and none of them
# are worth breaking a host's import for.
try:
    from eidetic.memory.backend import get_backend as _eidetic_get_backend
    from eidetic.memory.record import Record as _EideticRecord
    from eidetic.memory.scope import Scope as _EideticScope
    from eidetic.memory.scoring import signal_strength as _eidetic_signal_strength
except Exception as exc:  # pragma: no cover - exercised via monkeypatch, not a real break
    _eidetic_get_backend = None  # type: ignore[assignment]
    _EideticRecord = None  # type: ignore[assignment,misc]
    _EideticScope = None  # type: ignore[assignment,misc]
    _eidetic_signal_strength = None  # type: ignore[assignment]
    _EIDETIC_IMPORT_ERROR: Optional[str] = f"{type(exc).__name__}: {exc}"
else:
    _EIDETIC_IMPORT_ERROR = None

try:
    from coherence.assess import assess as _coherence_assess
except Exception as exc:  # pragma: no cover - exercised via monkeypatch, not a real break
    _coherence_assess = None  # type: ignore[assignment]
    _COHERENCE_IMPORT_ERROR: Optional[str] = f"{type(exc).__name__}: {exc}"
else:
    _COHERENCE_IMPORT_ERROR = None

__all__ = [
    "DEFAULT_SCOPE",
    "DEFAULT_VISIBILITY",
    "DEFAULT_TOP_K",
    "DEFAULT_MODE",
    "DEFAULT_ALPHA",
    "DEFAULT_BACKEND",
    "SCOPE_NAMING_CONVENTION",
    "PRIVATE_REQUIRES_EXPLICIT_FLAG",
    "FULL_CONTINUITY",
    "PARTIAL_CONTINUITY",
    "NO_CONTINUITY",
    "CODE_IMPORT_FAILED",
    "CODE_NO_STORAGE_ANCHOR",
    "CODE_SUBSYSTEM_ERROR",
    "CODE_MALFORMED_RESULT",
    "CODE_INVALID_RECORD",
    "CODE_DOMAIN_UNAVAILABLE",
    "CODE_ARTIFACT_UNREADABLE",
    "CODE_REINFORCE_FAILED",
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

#: A batch embedder, ``list[str] -> list[list[float]]``. Threaded straight into
#: coherence; this module never calls it and never inspects a vector.
EmbedSeam = Callable[[list[str]], list[list[float]]]

# --- scope/visibility conventions, pinned per eidetic docs/contract.md §4 ---

#: Matches eidetic's own ``--scope`` argparse default (``remember``/``recall``
#: both default to ``"default"``). Not a naming policy — a caller building a
#: checkpoint (t14) is expected to pass the repo's resolved identity scope;
#: this is only the fallback when none is supplied, kept identical to what
#: eidetic itself would do.
DEFAULT_SCOPE = "default"

#: docs/contract.md §2: "Default: `public`, for in-repo team-shared records."
#: Pinned here explicitly (rather than relying on eidetic's default silently
#: agreeing) so a future divergence is caught by this module's drift test
#: instead of discovered by a misrouted record.
DEFAULT_VISIBILITY = "public"

#: docs/contract.md §1: "One agent-personal scope per repo, named by that
#: repo's culture.yaml top-level agent suffix." Documentary only: this module
#: never reads culture.yaml itself (that is :mod:`embodiment.identity`'s seam,
#: a layer up) — it records the convention so the drift test can pin it and so
#: a caller knows what a ``scope`` argument means.
SCOPE_NAMING_CONVENTION = "culture.yaml-suffix-per-repo"

#: docs/contract.md §2: private is one explicit ``visibility="private"`` away
#: from the public default — never silently inferred. Honoured by construction:
#: :data:`DEFAULT_VISIBILITY` is ``"public"`` and every private write requires
#: the caller to say so.
PRIVATE_REQUIRES_EXPLICIT_FLAG = True

DEFAULT_TOP_K = 5

#: eidetic's own recall defaults, mirrored so this seam behaves like the CLI it
#: replaced. ``hybrid`` touches the embed server; ``exact``/``keyword`` are
#: fully offline (``eidetic/memory/scoring.py``).
DEFAULT_MODE = "hybrid"
DEFAULT_ALPHA = 0.5
DEFAULT_BACKEND = "files"

# --- continuity mode vocabulary (issue #2: "recorded no-continuity mode") ---

FULL_CONTINUITY = "full"
PARTIAL_CONTINUITY = "partial"
NO_CONTINUITY = "no-continuity"

# --- degradation code vocabulary (stable, machine-branchable tokens) --------
#
# Adapted from the subprocess vocabulary. ``cli-not-found`` became
# ``import-failed``; ``nonzero-exit``/``launch-error`` collapsed into
# ``subsystem-error``; ``malformed-json`` became ``malformed-result`` (there is
# no JSON on the wire any more); ``timeout`` is gone entirely because an
# in-process call cannot honestly claim one.

#: The subsystem's package could not be imported (missing or broken install).
CODE_IMPORT_FAILED = "import-failed"
#: No ``data_dir`` was supplied — trap #1's refusal, raised before any work.
CODE_NO_STORAGE_ANCHOR = "no-storage-anchor"
#: The sibling library raised. ``Degradation.exception`` names the type.
CODE_SUBSYSTEM_ERROR = "subsystem-error"
#: The sibling library returned a shape this seam cannot read.
CODE_MALFORMED_RESULT = "malformed-result"
#: The caller's record is missing required keys or is not a mapping.
CODE_INVALID_RECORD = "invalid-record"
#: Trap #2 — a successful assess report with a non-empty ``unavailable`` map.
CODE_DOMAIN_UNAVAILABLE = "domain-unavailable"
#: coherence could not read the artifact (missing, a directory, bad encoding).
CODE_ARTIFACT_UNREADABLE = "artifact-unreadable"
#: Recall succeeded but its passive write-back did not; records are still returned.
CODE_REINFORCE_FAILED = "reinforce-failed"

# Cap on reason text lifted from a subsystem's exception, so a runaway message
# cannot blow up a host's log or artifact.
_MAX_REASON_LEN = 500

# Environment keys the store pin touches. ``EIDETIC_DATA_DIR`` is the one this
# module sets; ``DR_DATA_DIR`` is written by eidetic's own ``_bridge_env`` on
# every store operation and never restored by it. Over a subprocess boundary
# that leak was invisible; in-process it would outlive the call, so both are
# saved and restored.
_STORE_ENV_KEYS = ("EIDETIC_DATA_DIR", "DR_DATA_DIR")

# Serialises this module's own eidetic calls so two threads cannot interleave
# their store pins. See :func:`_pinned_store` for what this does and does not
# protect.
_STORE_LOCK = threading.RLock()


@dataclass(frozen=True)
class Degradation:
    """One recorded, host-visible continuity degradation (constraint C3).

    Never inferred implicitly — every non-nominal path through this module
    constructs one of these and returns it on the outcome, rather than raising
    or silently returning an empty value that looks like success.

    Fields
    ------
    subsystem:
        ``"eidetic"`` or ``"coherence"``.
    stage:
        Which call degraded: ``"remember"``, ``"recall"``, ``"assess"`` or
        ``"probe"``.
    code:
        A stable machine token from the ``CODE_*`` vocabulary above — a caller
        branches on this, never on ``reason``'s free text.
    reason:
        Short human-readable cause, capped to :data:`_MAX_REASON_LEN` chars.
    exception:
        The exception class name, when one was caught (``None`` for a
        degradation that never reached the subsystem, e.g. an import failure or
        a missing storage anchor). This replaces the subprocess adapter's
        ``exit_code``, which has no in-process meaning.
    """

    subsystem: str
    stage: str
    code: str
    reason: str
    exception: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "subsystem": self.subsystem,
            "stage": self.stage,
            "code": self.code,
            "reason": self.reason,
        }
        if self.exception is not None:
            data["exception"] = self.exception
        return data


@dataclass(frozen=True)
class RememberOutcome:
    """Result of one :func:`remember` call.

    ``raw`` carries the record as actually upserted (eidetic's own
    ``Record.to_dict()``), so a host can see exactly what was stored — including
    the fields this seam defaulted.
    """

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
    """Result of one :func:`recall` call.

    ``records`` are eidetic's own ``Record.to_dict()`` dicts — byte-identical
    to what ``eidetic recall --json`` emitted, so consumers written against the
    subprocess adapter keep working. Ranking, relevance and freshness stay
    entirely inside eidetic; this module reads no score and computes none.

    ``ok`` can be ``True`` alongside a ``degradation``: a recall whose passive
    reinforcement write-back failed still returns its records
    (:data:`CODE_REINFORCE_FAILED`).
    """

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

    ``ok`` is ``True`` whenever a usable report was produced — **including a
    partial one**. A partial report (some domain listed in ``unavailable``) is
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

    ``mode`` is the mechanical classification of the two booleans below; it is
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


def eidetic_available() -> bool:
    """``True`` iff ``eidetic``'s memory API imported cleanly at module load."""
    return _EIDETIC_IMPORT_ERROR is None


def coherence_available() -> bool:
    """``True`` iff ``coherence``'s assess engine imported cleanly at module load."""
    return _COHERENCE_IMPORT_ERROR is None


def probe() -> ContinuityStatus:
    """Cheap availability check for both continuity subsystems.

    Does no work and touches no store — it reports the outcome of the guarded
    module-scope imports. A missing subsystem is itself a recorded
    :class:`Degradation` (never a silent ``False``), so a caller reading only
    ``ContinuityStatus.degradations`` still sees what is missing, and *why*,
    without inspecting the booleans separately.
    """
    degradations: list[Degradation] = []
    if _EIDETIC_IMPORT_ERROR is not None:
        degradations.append(_import_degradation("eidetic", "probe", _EIDETIC_IMPORT_ERROR))
    if _COHERENCE_IMPORT_ERROR is not None:
        degradations.append(_import_degradation("coherence", "probe", _COHERENCE_IMPORT_ERROR))
    return ContinuityStatus(
        eidetic_available=eidetic_available(),
        coherence_available=coherence_available(),
        degradations=tuple(degradations),
    )


# ---------------------------------------------------------------------------
# Degradation constructors (private)
# ---------------------------------------------------------------------------


def _import_degradation(subsystem: str, stage: str, error: str) -> Degradation:
    return Degradation(
        subsystem=subsystem,
        stage=stage,
        code=CODE_IMPORT_FAILED,
        reason=f"{subsystem} could not be imported: {error}"[:_MAX_REASON_LEN],
    )


def _error_degradation(
    subsystem: str, stage: str, exc: BaseException, *, code: str = CODE_SUBSYSTEM_ERROR
) -> Degradation:
    reason = str(exc) or type(exc).__name__
    return Degradation(
        subsystem=subsystem,
        stage=stage,
        code=code,
        reason=reason[:_MAX_REASON_LEN],
        exception=type(exc).__name__,
    )


def _malformed_degradation(subsystem: str, stage: str, reason: str) -> Degradation:
    return Degradation(
        subsystem=subsystem,
        stage=stage,
        code=CODE_MALFORMED_RESULT,
        reason=reason[:_MAX_REASON_LEN],
    )


def _storage_anchor_degradation(stage: str) -> Degradation:
    """Trap #1's refusal.

    Left unpinned, eidetic resolves a *public* write to the store directory
    inside whatever git repo the host process happens to be running in — so the
    host's memories would be committed into someone else's checkout. Rather
    than guess, this seam refuses and says so.

    The ``reason`` stays terse on purpose: a degradation reason is a short,
    machine-adjacent label a host will log or render, not the place for the
    essay. The essay is here and in the module docstring.
    """
    return Degradation(
        subsystem="eidetic",
        stage=stage,
        code=CODE_NO_STORAGE_ANCHOR,
        reason=(
            "no data_dir was supplied; refusing to touch the memory store with an "
            "unpinned location, which would resolve against the ambient git repo"
        ),
    )


# ---------------------------------------------------------------------------
# Trap #1 — the store pin
# ---------------------------------------------------------------------------


@contextmanager
def _pinned_store(data_dir: _StrPath) -> Iterator[None]:
    """Pin eidetic's store to *data_dir* for the duration of the block.

    ``EIDETIC_DATA_DIR`` is eidetic's only explicit store-location input, and it
    is the *first* thing both ``_resolve_write_dir`` and ``_candidate_read_dirs``
    consult — setting it short-circuits the ``git rev-parse --show-toplevel``
    probe entirely, for reads and writes, public and private alike. That is
    exactly what trap #1 needs: no ambient cwd can influence where a record
    lands.

    Both :data:`_STORE_ENV_KEYS` are snapshotted and restored in a ``finally``,
    so the host's environment is byte-identical afterwards whether the block
    returned or raised. ``DR_DATA_DIR`` is included because eidetic's own
    ``_bridge_env`` writes it on every store operation and never restores it —
    a leak that only becomes visible once the call is in-process.

    **Residual caveat, stated rather than hidden:** ``os.environ`` is
    process-global. :data:`_STORE_LOCK` serialises *this module's* eidetic
    calls, so embodiment can never race itself, but it cannot stop a third
    party from reading ``EIDETIC_DATA_DIR`` from another thread inside the
    window. That risk is inherent to eidetic's env-var-based store selection
    and cannot be closed from this side; the window is kept as short as
    possible instead.
    """
    with _STORE_LOCK:
        saved = {key: os.environ.get(key) for key in _STORE_ENV_KEYS}
        os.environ["EIDETIC_DATA_DIR"] = str(data_dir)
        try:
            yield
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


# ---------------------------------------------------------------------------
# eidetic seam — remember / recall
# ---------------------------------------------------------------------------


def _build_record(
    record: Mapping[str, Any],
    *,
    scope: str,
    visibility: str,
    added_by: Optional[str],
) -> Any:
    """Shape *record* into an eidetic ``Record``, or raise ``ValueError``.

    Deliberately thin. Validation mirrors eidetic's own ``remember`` CLI
    (``id``/``text``/``type`` required; a caller-supplied ``score`` is dropped
    because score is a recall-time artefact, never stored), and construction
    delegates to eidetic's own ``Record.from_dict`` so every remaining field —
    ``metadata``, ``links``, ``supersedes``, ``lifecycle``, ``recall_count`` —
    round-trips through eidetic's deserialiser rather than a copy of it here.
    """
    if not isinstance(record, Mapping):
        raise ValueError(f"record must be a mapping, got {type(record).__name__}")

    data = dict(record)
    missing = [key for key in ("id", "text", "type") if key not in data]
    if missing:
        raise ValueError(f"record missing required key(s): {', '.join(missing)}")

    data.pop("score", None)

    if "scope" in data:
        inline = data["scope"]
        if not isinstance(inline, Mapping) or "name" not in inline or "visibility" not in inline:
            raise ValueError(
                "record 'scope' must be an object with 'name' and 'visibility'; "
                "omit it to use the scope/visibility arguments instead"
            )
        data["scope"] = dict(inline)
    else:
        data["scope"] = {"name": scope, "visibility": visibility}

    data.setdefault("hash", "")
    data.setdefault("metadata", {})
    data.setdefault("created", datetime.now(timezone.utc).isoformat())
    data.setdefault("added_by", added_by)

    built = _EideticRecord.from_dict(data)
    built.score = None
    return built


def remember(
    record: Mapping[str, Any],
    *,
    data_dir: Optional[_StrPath] = None,
    scope: str = DEFAULT_SCOPE,
    visibility: str = DEFAULT_VISIBILITY,
    added_by: Optional[str] = None,
    backend: str = DEFAULT_BACKEND,
) -> RememberOutcome:
    """Ingest *record* into the eidetic memory store.

    *record* is shaped per eidetic's own contract (``id``, ``text``, ``type``
    required; ``metadata``, ``links``, ``supersedes``, ``lifecycle`` and an
    inline ``scope`` optional). This seam shapes no records of its own — it
    validates, fills the same defaults eidetic's CLI would, and hands the result
    to eidetic's ``Record.from_dict``.

    *data_dir* is **required** (trap #1). Passing nothing degrades to
    :data:`CODE_NO_STORAGE_ANCHOR` without touching eidetic at all. That check
    runs *before* the import check on purpose: it is the safety-critical
    refusal and must fire identically whether or not eidetic is installed.

    Never raises: a missing subsystem, an invalid record, or a store that
    raises all degrade to ``RememberOutcome(ok=False, …)`` with a
    :class:`Degradation` attached.
    """
    if data_dir is None:
        return RememberOutcome(
            ok=False, record_id=None, degradation=_storage_anchor_degradation("remember")
        )
    if _EIDETIC_IMPORT_ERROR is not None:
        return RememberOutcome(
            ok=False,
            record_id=None,
            degradation=_import_degradation("eidetic", "remember", _EIDETIC_IMPORT_ERROR),
        )

    try:
        built = _build_record(record, scope=scope, visibility=visibility, added_by=added_by)
    except (ValueError, TypeError, KeyError) as exc:
        return RememberOutcome(
            ok=False,
            record_id=None,
            degradation=_error_degradation("eidetic", "remember", exc, code=CODE_INVALID_RECORD),
        )

    try:
        with _pinned_store(data_dir):
            _eidetic_get_backend(backend).upsert(built)
    except Exception as exc:  # noqa: BLE001 - a store failure never reaches the host
        return RememberOutcome(
            ok=False, record_id=None, degradation=_error_degradation("eidetic", "remember", exc)
        )

    return RememberOutcome(ok=True, record_id=built.id, degradation=None, raw=built.to_dict())


def _visible(records: Sequence[Any], *, include_shadowed: bool, include_archived: bool) -> list:
    """Drop shadowed/archived records unless the caller asked for them.

    Mirrors ``eidetic recall``'s own default (both are hidden without an
    explicit flag) and reads eidetic's own ``lifecycle`` vocabulary — no
    lifecycle policy is defined here, only the CLI's documented default applied
    to the field eidetic already owns.
    """
    kept = []
    for record in records:
        lifecycle = getattr(record, "lifecycle", "active")
        if lifecycle == "shadowed" and not include_shadowed:
            continue
        if lifecycle == "archived" and not include_archived:
            continue
        kept.append(record)
    return kept


def _reinforce(backend: Any, hits: Sequence[Any], now: datetime) -> None:
    """Passively reinforce *hits* — eidetic's documented recall behaviour.

    Bumps ``recall_count``/``last_recall`` on **copies**, so the dicts already
    handed back to the caller keep their query-time (pre-bump) state, exactly
    as ``eidetic recall`` does. Query-time fields are cleared on the copy so
    reinforcement writes durable state only.
    """
    now_iso = now.isoformat()
    for hit in hits:
        bumped = copy.copy(hit)
        bumped.recall_count = hit.recall_count + 1
        bumped.last_recall = now_iso
        bumped.score = None
        bumped.signal = None
        backend.upsert(bumped)


def recall(
    query: str,
    *,
    data_dir: Optional[_StrPath] = None,
    scope: str = DEFAULT_SCOPE,
    visibility: str = DEFAULT_VISIBILITY,
    top_k: int = DEFAULT_TOP_K,
    mode: str = DEFAULT_MODE,
    alpha: float = DEFAULT_ALPHA,
    case_sensitive: bool = False,
    filters: Optional[Mapping[str, Any]] = None,
    include_shadowed: bool = False,
    include_archived: bool = False,
    reinforce: bool = True,
    now: Optional[datetime] = None,
    backend: str = DEFAULT_BACKEND,
) -> RecallOutcome:
    """Search the eidetic memory store.

    Same storage-anchor requirement as :func:`remember` (trap #1): *data_dir* is
    required. Returns eidetic's own ``Record.to_dict()`` dicts — the exact shape
    ``eidetic recall --json`` emitted — so consumers of the subprocess adapter
    are unaffected.

    Ranking is entirely eidetic's: *mode*, *alpha* and *case_sensitive* are
    passed through untouched and this module reads no score. Lifecycle
    exclusion and top-k are applied in eidetic's documented CLI order
    (lifecycle filter first, so *top_k* counts only visible records), and the
    freshness ``signal`` is stamped with eidetic's own ``signal_strength``.

    *now* is injectable so a host's recall stays deterministic; it defaults to
    the current UTC time. *reinforce* controls eidetic's passive reinforcement
    (a hit bumps ``recall_count``): a failure there is recorded as
    :data:`CODE_REINFORCE_FAILED` but never costs the caller its records.

    Never raises — see :func:`remember` for the degradation ladder this mirrors.
    """
    if data_dir is None:
        return RecallOutcome(
            ok=False, records=[], degradation=_storage_anchor_degradation("recall")
        )
    if _EIDETIC_IMPORT_ERROR is not None:
        return RecallOutcome(
            ok=False,
            records=[],
            degradation=_import_degradation("eidetic", "recall", _EIDETIC_IMPORT_ERROR),
        )

    moment = now if now is not None else datetime.now(timezone.utc)
    degradation: Optional[Degradation] = None

    try:
        with _pinned_store(data_dir):
            store = _eidetic_get_backend(backend)
            # Fetch unbounded, then apply the lifecycle filter before slicing —
            # eidetic's CLI order, so top_k counts only records the caller may see.
            found = store.search(
                query,
                2**31,
                _EideticScope(scope, visibility),
                dict(filters) if filters else None,
                mode,
                alpha=alpha,
                case_sensitive=case_sensitive,
            )
            if not isinstance(found, Sequence) or any(
                not isinstance(hit, _EideticRecord) for hit in found
            ):
                return RecallOutcome(
                    ok=False,
                    records=[],
                    degradation=_malformed_degradation(
                        "eidetic", "recall", "search did not return eidetic Record objects"
                    ),
                )

            hits = _visible(
                found, include_shadowed=include_shadowed, include_archived=include_archived
            )[:top_k]
            for hit in hits:
                hit.signal = _eidetic_signal_strength(hit, moment)
            records = [hit.to_dict() for hit in hits]

            if reinforce and hits:
                try:
                    _reinforce(store, hits, moment)
                except Exception as exc:  # noqa: BLE001 - a read must survive a failed write-back
                    degradation = _error_degradation(
                        "eidetic", "recall", exc, code=CODE_REINFORCE_FAILED
                    )
    except Exception as exc:  # noqa: BLE001 - a store failure never reaches the host
        return RecallOutcome(
            ok=False, records=[], degradation=_error_degradation("eidetic", "recall", exc)
        )

    return RecallOutcome(ok=True, records=records, degradation=degradation)


# ---------------------------------------------------------------------------
# coherence seam — assess
# ---------------------------------------------------------------------------

# The file-read failures coherence's engine deliberately lets propagate. Mirrors
# ``coherence.cli._commands._artifact_io.FILE_ERRORS`` — in-process, embodiment
# is the boundary that converts them (see the module docstring).
_ARTIFACT_ERRORS = (FileNotFoundError, IsADirectoryError, UnicodeDecodeError, OSError)


def assess(
    path: _StrPath,
    *,
    embed_fn: Optional[EmbedSeam] = None,
    reference_date: Optional[date] = None,
) -> AssessOutcome:
    """Run every applicable coherence domain on the artifact at *path*.

    Trap #2 (the point of this function): ``coherence.assess`` **returns
    normally** when the embedding endpoint is unreachable — it names the
    affected domains in the report's ``unavailable`` map rather than raising.
    This function ALWAYS reads that field and records a :class:`Degradation`
    (:data:`CODE_DOMAIN_UNAVAILABLE`) whenever it is non-empty, regardless of
    the call having succeeded. ``AssessOutcome.ok`` alone must never be read as
    "nothing degraded".

    *embed_fn* is coherence's own injection seam, passed straight through; left
    unset, coherence uses its real HTTP embedder. *reference_date* defaults to
    today, matching coherence's CLI: its engine never reads the clock, so the
    boundary supplies the date — and in-process that boundary is here.

    Unlike :func:`remember`/:func:`recall`, no storage anchor is required:
    coherence reads *path* and talks to an embedding endpoint; it has no
    cwd-dependent store to land in.

    Never raises — a missing subsystem, an unreadable artifact, an engine error
    or a malformed report all degrade to ``AssessOutcome(ok=False, …)``.
    """
    if _COHERENCE_IMPORT_ERROR is not None:
        return AssessOutcome(
            ok=False,
            domains={},
            unavailable={},
            degradation=_import_degradation("coherence", "assess", _COHERENCE_IMPORT_ERROR),
        )

    kwargs: dict[str, Any] = {
        "reference_date": reference_date if reference_date is not None else date.today()
    }
    if embed_fn is not None:
        kwargs["embed_fn"] = embed_fn

    try:
        report = _coherence_assess(path, **kwargs)
    except _ARTIFACT_ERRORS as exc:
        return AssessOutcome(
            ok=False,
            domains={},
            unavailable={},
            degradation=_error_degradation(
                "coherence", "assess", exc, code=CODE_ARTIFACT_UNREADABLE
            ),
        )
    except Exception as exc:  # noqa: BLE001 - an engine failure never reaches the host
        return AssessOutcome(
            ok=False,
            domains={},
            unavailable={},
            degradation=_error_degradation("coherence", "assess", exc),
        )

    if not isinstance(report, Mapping):
        return AssessOutcome(
            ok=False,
            domains={},
            unavailable={},
            degradation=_malformed_degradation(
                "coherence",
                "assess",
                f"expected a report mapping, got {type(report).__name__}",
            ),
        )

    raw_unavailable = report.get("unavailable")
    unavailable = dict(raw_unavailable) if isinstance(raw_unavailable, Mapping) else {}
    raw_domains = report.get("domains")
    domains = dict(raw_domains) if isinstance(raw_domains, Mapping) else {}

    degradation: Optional[Degradation] = None
    if unavailable:
        # Trap #2: the call already returned successfully — that is precisely
        # why this branch reads the payload, never the absence of an exception.
        # A verdict with SOME domain unavailable is still ``ok`` (a usable,
        # partial report), but the degradation makes the gap visible to the
        # host instead of silently passing as a clean run.
        names = ", ".join(sorted(unavailable))
        degradation = Degradation(
            subsystem="coherence",
            stage="assess",
            code=CODE_DOMAIN_UNAVAILABLE,
            reason=f"domain(s) unavailable: {names}"[:_MAX_REASON_LEN],
        )

    return AssessOutcome(
        ok=True,
        domains=domains,
        unavailable=unavailable,
        degradation=degradation,
        raw=dict(report),
    )
